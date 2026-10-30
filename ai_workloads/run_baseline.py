"""Run one fixed config (default: the untouched baseline) N times and record everything.

    python run_baseline.py --dry-run              # show the exact command, run nothing
    python run_baseline.py --trials 3 --cooldown 120
    python run_baseline.py --config cfg.json --label my-cfg

Run type is chosen automatically: no --config -> BASELINE, with --config -> FIXED.
Output: runs/<timestamp>_llama-<model>_<config>_<baseline|fixed>/   (<config>: --label, default, best-<objective>)
    REPORT.md            human-readable: run type, command, settings, system, results, run conditions
    meta.json            run type, workload, config, argv, env overrides, system snapshot (before)
    trial_NN.json        one Measurement per trial (incl. raw llama bench JSON)
    system_after.json    snapshot after the last trial (thermal drift, governor changes)
    summary.json         mean / sd / min / max across trials, with units
"""
import argparse
import statistics
import time
from dataclasses import asdict

from test_helper_functions import knobs as K
from test_helper_functions import report as R
from test_helper_functions import system
from test_helper_functions import workload as W
from test_helper_functions.cli import config_args, config_name, dump, load_config, shell_command, workload_args, workload_from


def summarize(ms):
    ok = [m for m in ms if m.ok]
    out = {"trials": len(ms), "ok": len(ok)}
    for f, (what, unit, better) in R.METRICS.items():
        xs = [getattr(m, f) for m in ok]
        if xs:
            out[f] = {"mean": statistics.fmean(xs),
                      "sd": statistics.stdev(xs) if len(xs) > 1 else 0.0,
                      "min": min(xs), "max": max(xs),
                      "unit": unit, "better": better, "meaning": what}
    return out


def write_report(out, kind, a, wl, cfg, reg, cmd, before, after, ms, summary):
    lines = [f"# {out.name}", "", f"**{R.RUN_TYPES[kind]}**", "",
             "## What was run", "",
             f"- workload: {R.describe_workload(wl)}",
             f"- trials: {a.trials} full llama bench invocations, {a.cooldown}s cooldown between them",
             f"- config: {R.describe_config(cfg, reg)}",
             f"- command: `{cmd}`", ""]
    eff = R.effective_settings(next((m.raw for m in ms if m.ok), []))
    if eff:
        lines += ["## Settings llama bench actually used", "",
                  "(includes defaults it resolved itself, e.g. -1 = auto/all)", ""]
        lines += [f"- {k}: {v}" for k, v in eff.items()] + [""]
    lines += ["## System before", "", *R.system_lines(before), ""]
    if after:
        lines += ["## System after", "", *R.system_lines(after), ""]
    if summary:
        lines += [f"## Results ({summary['ok']}/{summary['trials']} trials ok)", "",
                  *R.metrics_table(summary), ""]
    lines += ["## Per trial", "", f"| trial | {R.MEASUREMENT_COLS} |", "|---|---|---|---|---|---|"]
    lines += [f"| {i} | {R.measurement_cells(m)} |" for i, m in enumerate(ms)] + [""]
    lines += ["## Run conditions", ""] + [f"- {x}" for x in R.conditions(before, after)]
    R.write(out / "REPORT.md", lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    workload_args(p)
    config_args(p)
    p.add_argument("--label", help="config name in the run folder name (default: default / best-<objective> / config file stem)")
    p.add_argument("--trials", type=int, default=3, help="full llama bench invocations")
    p.add_argument("--cooldown", type=int, default=60, help="seconds between trials")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    reg = K.load(a.platform)
    wl = workload_from(a)
    cfg = load_config(p, a, reg)
    kind = "fixed" if cfg else "baseline"

    argv, env = W.build(cfg, reg, wl)
    cmd, env_over = shell_command(argv, env)
    print(R.banner(kind, [
        f"workload : {R.describe_workload(wl)}",
        f"trials   : {a.trials} x llama bench, {a.cooldown}s cooldown between trials",
        f"config   : {R.describe_config(cfg, reg)}",
        f"command  : {cmd}"]))
    if a.dry_run:
        print("(dry run: nothing executed, nothing written)")
        return

    before = system.snapshot(reg)
    out = R.run_dir(wl.model, config_name(a), kind)
    dump(out / "meta.json", {"run_type": kind, "description": R.RUN_TYPES[kind],
                             "workload": asdict(wl), "config": cfg, "argv": argv,
                             "env": env_over, "trials": a.trials, "cooldown_s": a.cooldown,
                             "system_before": before})
    print(f"output   : {out}\n")

    ms = []
    for i in range(a.trials):
        if i:
            print(f"  cooldown {a.cooldown}s ...", flush=True)
            time.sleep(a.cooldown)
        print(f"[{kind}] trial {i + 1}/{a.trials} running ...", flush=True)
        m = W.run(cfg, reg, wl)
        ms.append(m)
        dump(out / f"trial_{i:02d}.json", m.to_dict())
        print(f"[{kind}] trial {i + 1}/{a.trials}: {R.fmt_measurement(m)}", flush=True)

    after = system.snapshot(reg)
    dump(out / "system_after.json", after)
    s = summarize(ms)
    dump(out / "summary.json", {"run_type": kind, **s})
    write_report(out, kind, a, wl, cfg, reg, cmd, before, after, ms, s)

    print(f"\n{kind.upper()} result: {s['ok']}/{s['trials']} trials ok")
    for f, (what, unit, better) in R.METRICS.items():
        if f in s:
            print(f"  {f:8s} {s[f]['mean']:10.3f} ± {s[f]['sd']:<8.3f} {unit:6s} ({what}; {better} is better)")
    print(f"\nreport: {out / 'REPORT.md'}")


if __name__ == "__main__":
    main()
