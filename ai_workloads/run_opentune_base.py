"""Plain OpenTuner search over the knob registry (the non-LLM baseline tuner).

    python run_opentune_base.py --objective throughput --baseline runs/<baseline dir>/summary.json \
                   --test-limit 50 --stop-after 7200
    python run_opentune_base.py --objective weighted --weight 0.7 --space llm_space.json ...
    python run_opentune_base.py --print-search-space-size        # size only, runs nothing

  --objective   throughput | latency | weighted (normalised to the baseline, see OBJECTIVES below)
  --space       JSON from the LLM advisor: enabled knobs, narrowed ranges, seed configs
                (run type becomes OPENTUNE GUIDED)
  --platform    wsl | windows | linux-native: params/<platform>.toml is loaded with params/llama.toml
  --allow-system  also tune system knobs: sysfs (needs passwordless sudo for tee) and the
                  Windows power plan (powercfg); state is snapshotted first and restored on exit
All standard OpenTuner flags (--technique, --test-limit, --stop-after, --database ...) work.

Output: runs/<timestamp>_llama-<model>_<opentune-base|opentune-guided>_<objective>/
    REPORT.md          run type, objective, baseline, space, best config vs baseline, top configs
                       (rewritten after every evaluation, so it is valid mid-run)
    meta.json          run type, args, space, baseline, system snapshot
    progress.png       score per evaluation + best so far (plot_opentune.py, at the end)
    trials.jsonl       one line per evaluation actually executed (or rejected by a constraint):
                       config, measurement, score, error, elapsed_s (since tuning start), finished_at
    best_config.json   written when OpenTuner finishes
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import opentuner
from opentuner import ConfigurationManipulator, MeasurementInterface, Result
from opentuner.search.manipulator import (BooleanParameter, EnumParameter, IntegerParameter,
                                          PowerOfTwoParameter)
from opentuner.search.objective import MaximizeAccuracy
from opentuner.tuningrunmain import TuningRunMain

from test_helper_functions import knobs as K
from test_helper_functions import report as R
from test_helper_functions import system
from test_helper_functions import workload as W
from test_helper_functions.cli import dump, workload_args, workload_from

PARAM = {"int": lambda k: IntegerParameter(k.name, k.min, k.max),
         "pow2": lambda k: PowerOfTwoParameter(k.name, k.min, k.max),
         "enum": lambda k: EnumParameter(k.name, k.values),
         "bool": lambda k: BooleanParameter(k.name)}

# Objective: Measurement -> one score, normalised to the baseline (1.0 = baseline, higher is
# better), so throughput (tok/s) and latency (s) can be mixed in the weighted objective.
OBJECTIVES = {
    "throughput": lambda m, b, w: m.tg_tps / b.tg_tps,
    "latency": lambda m, b, w: b.ttft_s / m.ttft_s,
    "weighted": lambda m, b, w: w * m.tg_tps / b.tg_tps + (1 - w) * b.ttft_s / m.ttft_s,
}
OBJECTIVE_DOC = {
    "throughput": "score = tg_tps / baseline tg_tps (1.0 = baseline, higher is better)",
    "latency": "score = baseline ttft_s / ttft_s (1.0 = baseline, higher is better)",
    "weighted": "score = w * throughput score + (1 - w) * latency score, w = {w}",
}


class LlamaBenchTuner(MeasurementInterface):
    def __init__(self, args):
        super().__init__(args, program_name="llama-bench")
        space = K.load_space(args.space)
        self.kind = "opentune-guided" if args.space else "opentune-base"
        self.reg = K.apply_space(K.load(args.platform), space)
        self.seeds = space.get("seeds", [])
        self.space = {n: k for n, k in self.reg.items() if k.enabled
                      and (args.allow_system or k.layer not in K.SYSTEM_LAYERS)}
        self.wl = workload_from(args)
        self.snap = system.snapshot(self.reg)
        self.out = self.base = self.base_src = None   # created lazily on the first run()
        self.touched = set()                          # system knobs written so far
        self.records = []                             # (cfg, Measurement | None, score, error)
        self.t0 = None

    def _start(self):
        args = self.args
        if args.baseline:
            s = json.loads(Path(args.baseline).read_text())
            self.base = W.Measurement(ok=True, **{f: s[f]["mean"] for f in R.METRICS if f in s})
            self.base_src = f"{args.baseline} ({s.get('run_type', '?')} run, mean of {s['ok']} trials)"
        else:
            print(f"[{self.kind}] no --baseline given: measuring defaults once ...", flush=True)
            self.base = W.run({}, self.reg, self.wl)
            self.base_src = "measured once at the start of this run (no --baseline given)"
        self.out = R.run_dir(self.wl.model, self.kind, args.objective)
        self.t0 = time.monotonic()
        dump(self.out / "meta.json", {
            "run_type": self.kind, "description": R.RUN_TYPES[self.kind],
            "objective": args.objective, "objective_formula": self._objective_doc(),
            "args": vars(args), "space": list(self.space), "seeds": self.seeds,
            "baseline": self.base.to_dict(), "baseline_source": self.base_src,
            "system_before": self.snap})
        print(R.banner(self.kind, [
            f"objective: {self._objective_doc()}",
            f"workload : {R.describe_workload(self.wl)}",
            f"space    : {len(self.space)} knobs: {', '.join(self.space)}",
            f"seeds    : defaults + {len(self.seeds)} from space file",
            f"baseline : {R.fmt_measurement(self.base)}",
            f"           from {self.base_src}",
            f"output   : {self.out}"]), flush=True)

    def _objective_doc(self):
        return OBJECTIVE_DOC[self.args.objective].format(w=self.args.weight)

    def manipulator(self):
        m = ConfigurationManipulator()
        for k in self.space.values():
            m.add_parameter(PARAM[k.type](k))
        return m

    def objective(self):
        return MaximizeAccuracy()

    def seed_configurations(self):
        # Registry defaults are always a seed, so the search starts at the baseline.
        start = {n: k.default if k.default is not None else k.min for n, k in self.space.items()}
        return [start] + [{**start, **s} for s in self.seeds]

    def run(self, desired_result, input, limit):
        if self.base is None:
            self._start()
        cfg = desired_result.configuration.data
        rec = {"eval": len(self.records) + 1, "requestor": desired_result.requestor,
               "config": cfg, "changed_vs_default": R.config_diff(cfg, self.reg)}
        m, err = None, None
        if errs := K.violations(cfg):
            err = "constraint: " + "; ".join(errs)
        else:
            try:
                self.touched |= {n for n in cfg if self.reg[n].layer in K.SYSTEM_LAYERS}
                system.apply(cfg, self.reg)
                m = W.run(cfg, self.reg, self.wl)
                rec["measurement"] = {k: v for k, v in m.to_dict().items() if k != "raw"}
                if not m.ok:
                    err = m.error.strip().splitlines()[-1] if m.error.strip() else "failed"
            except PermissionError as e:
                err = str(e)
        score = (OBJECTIVES[self.args.objective](m, self.base, self.args.weight)
                 if m and m.ok else 0.0)
        rec.update(score=score, error=err, elapsed_s=round(time.monotonic() - self.t0, 2),
                   finished_at=datetime.now().isoformat(timespec="seconds"))
        with open(self.out / "trials.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.records.append((cfg, m, score, err))
        self._print_eval(rec, m)
        self._write_report()
        if not (m and m.ok):
            return Result(state="ERROR", time=float("inf"), accuracy=0.0)
        return Result(time=m.wall_s, accuracy=score)

    def _best(self):
        ok = [r for r in self.records if r[1] and r[1].ok]
        return max(ok, key=lambda r: r[2]) if ok else None

    def _print_eval(self, rec, m):
        best = self._best()
        head = f"[{self.kind}] eval {rec['eval']:3d} ({rec['requestor']})"
        if rec["error"] and not (m and m.ok):
            print(f"{head}: SKIPPED/FAILED: {rec['error']}", flush=True)
        else:
            print(f"{head}: score {rec['score']:.3f} (best {best[2]:.3f}) | {R.fmt_measurement(m)}",
                  flush=True)
        print(f"{' ' * len(head)}  changed vs default: {rec['changed_vs_default'] or 'nothing'}",
              flush=True)

    def _write_report(self):
        a = self.args
        n_ok = sum(1 for r in self.records if r[1] and r[1].ok)
        mins = (time.monotonic() - self.t0) / 60
        lines = [f"# {self.out.name}", "", f"**{R.RUN_TYPES[self.kind]}**", "",
                 "## Setup", "",
                 f"- objective: `{a.objective}`: {self._objective_doc()}",
                 f"- workload: {R.describe_workload(self.wl)}",
                 f"- search space: {len(self.space)} knobs: {', '.join(self.space)}",
                 f"- space file: {a.space or 'none (default registry)'}; "
                 f"seeds: defaults + {len(self.seeds)}",
                 f"- system knobs tuned: {'yes' if a.allow_system else 'no'}",
                 f"- budget: test-limit {a.test_limit or 'none'}, "
                 f"stop-after {f'{a.stop_after}s' if a.stop_after else 'none'}", "",
                 "## Baseline", "", f"- source: {self.base_src}",
                 f"- {R.fmt_measurement(self.base)}", "",
                 "## Progress", "",
                 f"- evaluations executed: {n_ok} ok, {len(self.records) - n_ok} failed/rejected, "
                 f"{mins:.1f} min elapsed",
                 "- (OpenTuner's own test count also includes duplicate requests, "
                 "which are answered from its database and not re-run)", ""]
        best = self._best()
        if best:
            cfg, m, score, _ = best
            lines += ["## Best so far vs baseline", "",
                      f"- score: **{score:.3f}** (baseline = 1.000)",
                      f"- changed vs default: {R.config_diff(cfg, self.reg) or 'nothing'}", "",
                      "| metric | baseline | best | change |", "|---|---|---|---|"]
            for f, (what, unit, better) in R.METRICS.items():
                b, n = getattr(self.base, f), getattr(m, f)
                if b:
                    lines.append(f"| {f} ({unit}, {better} is better) | {b:.3f} | {n:.3f} "
                                 f"| {R.pct(n, b, better)} |")
            lines += ["", "## Top 5 evaluations", "",
                      f"| score | {R.MEASUREMENT_COLS} | changed vs default |",
                      "|---|---|---|---|---|---|---|"]
            top = sorted((r for r in self.records if r[1] and r[1].ok), key=lambda r: -r[2])[:5]
            lines += [f"| {s:.3f} | {R.measurement_cells(mm)} | {R.config_diff(c, self.reg) or 'defaults'} |"
                      for c, mm, s, _ in top]
        lines += ["", "## Run conditions (at start)", ""]
        lines += [f"- {x}" for x in R.conditions(self.snap)]
        R.write(self.out / "REPORT.md", lines)

    def save_final_config(self, configuration):
        if self.out is None:
            return
        dump(self.out / "best_config.json", configuration.data)
        self._write_report()
        print(f"\n[{self.kind}] done. best config -> {self.out / 'best_config.json'}")
        print(f"[{self.kind}] report      -> {self.out / 'REPORT.md'}")


def main():
    p = argparse.ArgumentParser(parents=opentuner.argparsers(), description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    workload_args(p)
    p.add_argument("--objective", choices=OBJECTIVES, default="throughput")
    p.add_argument("--weight", type=float, default=0.5, help="throughput weight for 'weighted'")
    p.add_argument("--baseline", help="summary.json from run_baseline.py")
    p.add_argument("--space", help="space JSON (LLM advisor output)")
    p.add_argument("--allow-system", action="store_true")
    args = p.parse_args()
    if not args.database:       # OpenTuner's default is opentuner.db/<host>.db but it never creates the dir
        Path("opentuner.db").mkdir(exist_ok=True)

    tuner = LlamaBenchTuner(args)
    try:
        TuningRunMain(tuner, args).main()
    finally:
        if tuner.touched:
            system.restore(tuner.snap, tuner.reg, tuner.touched)
            print(f"system knobs restored: {sorted(tuner.touched)}")
        if tuner.out and (tuner.out / "trials.jsonl").exists():   # progress.png, also after Ctrl+C
            subprocess.run([sys.executable, str(Path(__file__).with_name("plot_opentune.py")),
                            str(tuner.out / "trials.jsonl")])


if __name__ == "__main__":
    main()
