"""Human-readable output shared by run_baseline.py and tune.py.

Every run directory is named <timestamp>-<run type>[-<label>] and gets a
REPORT.md that says what kind of run it was, what was executed, under which
system state, and what came out (with units).
"""
from datetime import datetime
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"

RUN_TYPES = {
    "baseline": "BASELINE: default configuration. No knobs set; llama.cpp and OS defaults.",
    "fixed": "FIXED CONFIG: one user-supplied configuration, measured the same way as a baseline.",
    "perf-stat": "PERF STAT: one benchmark run under `perf stat -I` (hardware counters over time) "
                 "plus a CPU frequency / temperature log.",
    "perf-record": "PERF RECORD: one benchmark run under `perf record -g` (sampled call-graph "
                   "profile: where the time goes).",
    "opentune-base": "OPENTUNE BASE: plain OpenTuner search over the default knob registry.",
    "opentune-guided": "OPENTUNE GUIDED: OpenTuner search over a space file "
                       "(enabled knobs / narrowed ranges / seed configs), e.g. LLM advisor output.",
}

# metric -> (what it is, unit, which direction is better)
METRICS = {
    "pp_tps": ("prompt processing speed", "tok/s", "higher"),
    "tg_tps": ("token generation speed (throughput)", "tok/s", "higher"),
    "ttft_s": ("time to first token = n_prompt / pp_tps (latency proxy)", "s", "lower"),
    "tpot_ms": ("time per output token = 1000 / tg_tps", "ms", "lower"),
    "wall_s": ("wall time of the whole llama bench call", "s", "lower"),
}

# llama bench JSON fields worth reporting as the settings that actually ran
EFFECTIVE = ("backends", "devices", "gpu_info", "cpu_info", "model_type", "model_size",
             "n_threads", "cpu_mask", "poll", "n_gpu_layers", "n_batch", "n_ubatch",
             "flash_attn", "type_k", "type_v", "load_mode", "repack", "no_kv_offload",
             "build_commit")


def run_dir(kind, label=None):
    assert kind in RUN_TYPES, kind
    d = RUNS / (f"{datetime.now():%Y%m%d-%H%M%S}-{kind}" + (f"-{label}" if label else ""))
    d.mkdir(parents=True)
    return d


def banner(kind, lines=()):
    bar = "=" * 72
    return "\n".join([bar, RUN_TYPES[kind], *lines, bar])


def describe_workload(wl):
    return (f"llama bench, model {Path(wl.model).name}: "
            f"prompt-processing test ({wl.n_prompt} prompt tokens) and "
            f"token-generation test ({wl.n_gen} generated tokens), "
            f"context depth {wl.n_depth}, {wl.reps} repetitions each, "
            f"{wl.delay}s delay between tests")


def describe_config(cfg, reg):
    if not cfg:
        return "{} (nothing changed: all defaults)"
    return ", ".join(f"{n}={v} (default {reg[n].default})" for n, v in cfg.items())


def config_diff(cfg, reg):
    """Only the knobs whose value differs from the registry default."""
    return {n: v for n, v in cfg.items() if v != reg[n].default}


def effective_settings(raw):
    return {k: raw[0][k] for k in EFFECTIVE if raw and k in raw[0]}


def fmt_measurement(m):
    if not m.ok:
        return f"FAILED: {m.error.strip().splitlines()[-1] if m.error.strip() else 'unknown'}"
    return (f"gen {m.tg_tps:7.2f} tok/s | prompt {m.pp_tps:8.1f} tok/s | "
            f"TTFT {m.ttft_s:6.3f} s | {m.tpot_ms:6.1f} ms/token | wall {m.wall_s:4.0f} s")


MEASUREMENT_COLS = "gen tok/s | prompt tok/s | TTFT s | ms/token | wall s"


def measurement_cells(m):
    """Table cells matching MEASUREMENT_COLS (fmt_measurement contains '|', so not for tables)."""
    if not m.ok:
        return f"{fmt_measurement(m)} | | | |"
    return f"{m.tg_tps:.2f} | {m.pp_tps:.1f} | {m.ttft_s:.3f} | {m.tpot_ms:.1f} | {m.wall_s:.0f}"


def pct(new, old, better):
    if not old:
        return "n/a"
    d = (new - old) / old * 100
    good = d > 0 if better == "higher" else d < 0
    return f"{d:+.1f}% ({'better' if good else 'worse' if d else 'same'})"


def conditions(before, after=None):
    """Conditions of a run (power, load, changed knobs, temperatures), for comparing runs later."""
    c = []
    if before.get("on_ac_power") is False:
        c.append("on battery power")
    c.append(f"load average before (1/5/15 min): {' '.join((before.get('loadavg') or '?').split()[:3])}")
    if after:
        changed = {k: (v, after["system"].get(k)) for k, v in before.get("system", {}).items()
                   if after["system"].get(k) != v}
        if changed:
            c.append(f"system knobs changed during the run (before, after): {changed}")
        for zone, t0 in before.get("thermal_c", {}).items():
            if (t1 := after.get("thermal_c", {}).get(zone)) is not None:
                c.append(f"temperature {zone}: {t0:.0f} C before, {t1:.0f} C after")
    return c


def system_lines(snap):
    knobs = snap.get("system", {})
    return [f"- platform: {snap.get('platform')} | llama binary: {snap.get('llama_bin')}",
            f"- host: {snap.get('host')} | {snap.get('cpu')} | kernel {snap.get('kernel')}",
            f"- llama: {(snap.get('llama') or '').splitlines()[0] if snap.get('llama') else '?'}",
            f"- AC power: {snap.get('on_ac_power')} | load avg: {snap.get('loadavg')}",
            f"- CPU package temp: {snap.get('thermal_c', {}).get('x86_pkg_temp')} C",
            "- system knobs: " + (", ".join(f"{k}={v}" for k, v in knobs.items()) or "none")]


def metrics_table(summary):
    rows = ["| metric | meaning | unit | better | mean | sd | CV | min | max |",
            "|---|---|---|---|---|---|---|---|---|"]
    for f, (what, unit, better) in METRICS.items():
        s = summary.get(f)
        if not s:
            continue
        cv = s["sd"] / s["mean"] * 100 if s["mean"] else 0
        rows.append(f"| {f} | {what} | {unit} | {better} | {s['mean']:.3f} | {s['sd']:.3f} "
                    f"| {cv:.1f}% | {s['min']:.3f} | {s['max']:.3f} |")
    return rows


def write(path, lines):
    Path(path).write_text("\n".join(lines) + "\n")