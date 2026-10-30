"""Run the benchmark ONCE under perf: program properties of one config.

    python run_perf.py stat   --dry-run                 # show the commands, run nothing
    python run_perf.py stat                             # perf stat -I, 1000 ms intervals
    python run_perf.py stat   --interval 250 --events cycles,instructions
    python run_perf.py record                           # perf record -g, 999 Hz
    python run_perf.py stat   --config runs/<opentune run>/best_config.json

stat:   counters per interval. Metrics (IPC, miss rates, events per 1000 instructions) are
        computed PER INTERVAL (e.g. instructions_i / cycles_i), the first interval is
        discarded, then they are summarised (mean/median/min/max). Raw counts are reported
        unchanged, for debugging only.
record: sampled call graphs: where the CPU time goes.

Needs perf access (WSL default paranoid=2 counts user space only; for kernel too:
sudo sysctl kernel.perf_event_paranoid=1). Not available for --platform windows.

Output: runs/<timestamp>_llama-<model>_<config>_<perf-stat|perf-record>/   (<config>: --label, default, best-<objective>)
    REPORT.md, meta.json, system_after.json, bench.json (llama bench JSON of this run)
    stat:   perf_stat.csv  raw `perf stat -I -x,` output (all intervals, nothing dropped)
            intervals.csv  one row per interval: raw counts, then the per-interval metrics
                           (first row = the discarded interval)
            telemetry.csv  per interval: mean/min/max core MHz and package temp, if the host exposes them
            perf_stat.png  every metric per interval over time (made by plot_perf_stat.py)
    record: perf.data, top_symbols.txt
"""
import argparse
import glob
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from test_helper_functions import knobs as K
from test_helper_functions import platforms as P
from test_helper_functions import report as R
from test_helper_functions import system
from test_helper_functions import workload as W
from test_helper_functions.cli import config_args, config_name, dump, load_config, shell_command, workload_args, workload_from

EVENTS = ("cycles,instructions,cache-references,cache-misses,branches,branch-misses,"
          "context-switches,cpu-migrations,page-faults")
# Metrics computed per interval: (numerator event, denominator event, scale).
# Every other hardware event also gets "<event>_pki" = per 1000 instructions.
RATIOS = {"ipc": ("instructions", "cycles", 1),
          "cache_miss_rate": ("cache-misses", "cache-references", 1),
          "branch_miss_rate": ("branch-misses", "branches", 1)}
SOFTWARE = {"context-switches", "cpu-migrations", "page-faults", "minor-faults", "major-faults",
            "task-clock", "cpu-clock", "alignment-faults", "emulation-faults"}


def check_perf(p):
    if not shutil.which("perf"):
        p.error("perf not found (install linux-tools-$(uname -r))")
    level = int(Path("/proc/sys/kernel/perf_event_paranoid").read_text())
    if level > 2 and os.geteuid() != 0:
        p.error(f"kernel.perf_event_paranoid is {level}; perf cannot count as a normal user.\n"
                "  fix for this boot: sudo sysctl kernel.perf_event_paranoid=1")
    return level


# ------------------------------------------------------------------ stat ----
def sample_telemetry(path, interval_s, stop):
    """One row per interval: t, mean/min/max MHz across CPUs, package temp (empty if absent)."""
    temp = next((f"{z}/temp" for z in glob.glob("/sys/class/thermal/thermal_zone*")
                 if system._read(f"{z}/type") == "x86_pkg_temp"), None)
    freqs = sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"))
    t0 = time.monotonic()
    with open(path, "w") as f:
        f.write("t_s,mhz_mean,mhz_min,mhz_max,pkg_temp_c\n")
        while not stop.is_set():
            mhz = [int(v) / 1000 for v in map(system._read, freqs) if v]
            tc = int(system._read(temp)) / 1000 if temp else ""
            cols = f"{statistics.fmean(mhz):.0f},{min(mhz):.0f},{max(mhz):.0f}" if mhz else ",,"
            f.write(f"{time.monotonic() - t0:.2f},{cols},{tc}\n")
            f.flush()
            stop.wait(interval_s)


def parse_intervals(path):
    """perf stat -I -x, output -> [(t, {event: value})] in time order.
    Unsupported/uncounted events are left out of that interval."""
    rows = defaultdict(dict)
    for line in open(path):
        parts = line.strip().split(",")
        if line.startswith("#") or len(parts) < 4:
            continue
        try:
            t, value = float(parts[0]), float(parts[1])
        except ValueError:          # "<not supported>" / "<not counted>"
            continue
        rows[t][parts[3].split(":")[0]] = value
    return sorted(rows.items())


def metrics(ev):
    """Per-interval metrics from one interval's raw counts (raw counts are not changed)."""
    out = {name: ev[num] / ev[den] * k for name, (num, den, k) in RATIOS.items()
           if ev.get(den) and num in ev}
    if ev.get("instructions"):
        for e, v in ev.items():
            if e not in SOFTWARE and e not in ("cycles", "instructions"):
                out[f"{e}_pki"] = v / ev["instructions"] * 1000
    return out


def summary(xs):
    return (f"mean {statistics.fmean(xs):.4g} / median {statistics.median(xs):.4g} / "
            f"min {min(xs):.4g} / max {max(xs):.4g}")


def stats_row(name, xs):
    return (f"| {name} | {statistics.fmean(xs):.4g} | {statistics.median(xs):.4g} "
            f"| {min(xs):.4g} | {max(xs):.4g} | {len(xs)} |")


def run_stat(a, argv, env, out, wl):
    perf_argv = ["perf", "stat", "-I", str(a.interval), "-x", ",", "-e", a.events,
                 "-o", str(out / "perf_stat.csv"), "--"] + argv
    stop = threading.Event()
    sampler = threading.Thread(target=sample_telemetry, daemon=True,
                               args=(out / "telemetry.csv", a.interval / 1000, stop))
    sampler.start()
    t0 = time.monotonic()
    proc = subprocess.run(perf_argv, env=env, capture_output=True, text=True, timeout=wl.timeout)
    wall = time.monotonic() - t0
    stop.set()
    sampler.join()
    if proc.returncode != 0:
        return proc, wall, [], []

    intervals = parse_intervals(out / "perf_stat.csv")
    per = [metrics(ev) for _, ev in intervals]
    raw_names = sorted({e for _, ev in intervals for e in ev})
    met_names = [n for n in RATIOS if any(n in m for m in per)]
    met_names += sorted({n for m in per for n in m} - set(RATIOS))
    with open(out / "intervals.csv", "w") as f:
        f.write(",".join(["t_s", *raw_names, *met_names]) + "\n")
        for (t, ev), m in zip(intervals, per):
            f.write(",".join([f"{t}", *(f"{ev[n]:.0f}" if n in ev else "" for n in raw_names),
                              *(f"{m[n]:.6g}" if n in m else "" for n in met_names)]) + "\n")

    kept = per[1:]                  # first interval discarded
    setup = [f"- perf: `{' '.join(perf_argv[:9])}`, {a.interval} ms intervals, {wall:.0f} s wall",
             f"- intervals: {len(intervals)} recorded, first discarded, {len(kept)} used"]
    lines = ["## Metrics (per interval, first interval discarded)", "",
             "Each metric is computed per interval, then summarised. `_pki` = per 1000 instructions.", "",
             "| metric | mean | median | min | max | n |", "|---|---|---|---|---|---|"]
    for name in met_names:
        if xs := [m[name] for m in kept if name in m]:
            lines.append(stats_row(name, xs))
    totals = defaultdict(float)
    for _, ev in intervals:
        for e, v in ev.items():
            totals[e] += v
    lines += ["", "## Raw event counts (debugging only: all intervals, as reported by perf)", "",
              "| event | count |", "|---|---|", *[f"| {e} | {totals[e]:,.0f} |" for e in raw_names], ""]
    tel = [r.split(",") for r in open(out / "telemetry.csv").read().splitlines()[2:]]  # drop first
    mhz = [float(r[1]) for r in tel if r[1]]
    temps = [float(r[4]) for r in tel if r[4]]
    if mhz or temps:
        lines += ["## Frequency / temperature (telemetry.csv, first sample discarded)", ""]
        if mhz:
            lines.append(f"- mean core MHz: {summary(mhz)}")
        if temps:
            lines.append(f"- package temp C: {summary(temps)}")
        lines.append("")
    # Plots over time: plot_perf_stat.py stays a standalone script, so run it as one.
    plot = subprocess.run([sys.executable, str(Path(__file__).with_name("plot_perf_stat.py")),
                           str(out / "perf_stat.csv"), str(out / "perf_stat.png")],
                          capture_output=True, text=True)
    lines += ["Plots over time: perf_stat.png" if plot.returncode == 0 else
              f"Plot failed: {(plot.stderr.strip().splitlines() or ['?'])[-1]}", ""]
    return proc, wall, setup, lines


# ---------------------------------------------------------------- record ----
def run_record(a, argv, env, out, wl):
    # perf record cannot write its data file onto a Windows drive under WSL (/mnt/*:
    # "failed to write perf data, error: Bad address"), so record to the Linux filesystem
    # (/tmp) and copy perf.data into the run directory afterwards.
    tmp = Path(tempfile.mkdtemp(prefix="perf-record-", dir="/tmp"))
    try:                            # finally: also removes /tmp data on Ctrl+C
        data = tmp / "perf.data"
        perf_argv = ["perf", "record", "-g", "-F", str(a.freq), "-o", str(data), "--"] + argv
        t0 = time.monotonic()
        proc = subprocess.run(perf_argv, env=env, capture_output=True, text=True, timeout=wl.timeout)
        wall = time.monotonic() - t0
        if proc.returncode != 0:
            return proc, wall, [], []
        rep = subprocess.run(["perf", "report", "-i", str(data), "--stdio", "--no-children",
                              "--sort", "dso,symbol", "--percent-limit", "0.5"],
                             capture_output=True, text=True)
        shutil.copyfile(data, out / "perf.data")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    top = [l for l in rep.stdout.splitlines() if l.strip() and not l.startswith("#")
           and "%" in l.split()[0]][:a.top]
    return proc, wall, [f"- perf: `perf record -g -F {a.freq}`, {wall:.0f} s wall"], [
                        f"## Top {len(top)} symbols by self time", "", "```", *top, "```", "",
                        "Full list: top_symbols.txt; interactive: `perf report -i perf.data`.", ""]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=("stat", "record"))
    workload_args(p)
    config_args(p)
    p.add_argument("--label", help="config name in the run folder name (default: default / best-<objective> / config file stem)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--interval", type=int, default=1000, help="stat: perf stat -I interval (ms)")
    p.add_argument("--events", default=EVENTS, help="stat: comma-separated perf events")
    p.add_argument("--freq", type=int, default=999, help="record: sampling frequency (Hz)")
    p.add_argument("--top", type=int, default=40, help="record: symbols listed in REPORT.md")
    a = p.parse_args()
    kind = f"perf-{a.mode}"

    if P.select(a.platform).windows_exe:
        p.error(f"platform {a.platform} runs a Windows .exe; Linux perf cannot profile it")
    paranoid = check_perf(p) if not a.dry_run else None
    reg = K.load(a.platform)
    wl = workload_from(a)
    cfg = load_config(p, a, reg)
    argv, env = W.build(cfg, reg, wl)
    cmd, env_over = shell_command(argv, env)
    print(R.banner(kind, [f"workload : {R.describe_workload(wl)}",
                          f"config   : {R.describe_config(cfg, reg)}",
                          f"command  : perf {a.mode} ... -- {cmd}"]))
    if a.dry_run:
        print("(dry run: nothing executed, nothing written)")
        return

    before = system.snapshot(reg)
    out = R.run_dir(wl.model, config_name(a), kind)
    dump(out / "meta.json", {"run_type": kind, "description": R.RUN_TYPES[kind],
                             "workload": asdict(wl), "config": cfg, "argv": argv, "env": env_over,
                             "perf_event_paranoid": paranoid, "args": vars(a),
                             "system_before": before})
    print(f"output   : {out}\n[{kind}] running (one llama bench invocation) ...", flush=True)

    proc, wall, setup, body = (run_stat if a.mode == "stat" else run_record)(a, argv, env, out, wl)
    after = system.snapshot(reg)
    dump(out / "system_after.json", after)
    (out / "bench.json").write_text(proc.stdout)
    if proc.returncode != 0:
        (out / "stderr.txt").write_text(proc.stderr)
        print(f"[{kind}] FAILED (exit {proc.returncode}), see {out / 'stderr.txt'}")
        return

    m = W.parse(proc.stdout, wl)
    m.wall_s = wall
    R.write(out / "REPORT.md", [
        f"# {out.name}", "", f"**{R.RUN_TYPES[kind]}**", "",
        "## What was run", "",
        f"- workload: {R.describe_workload(wl)}",
        f"- config: {R.describe_config(cfg, reg)}",
        *setup, "",
        "## Benchmark result of this run (with profiling overhead)", "",
        f"- {R.fmt_measurement(m)}", "",
        *body,
        "## System before", "", *R.system_lines(before), "",
        "## Run conditions", "", *[f"- {x}" for x in R.conditions(before, after)]])

    print(f"[{kind}] {R.fmt_measurement(m)}")
    for l in body:
        if l.startswith("## Raw"):
            break
        if l.startswith("| ") and not l.startswith("| metric"):
            print(f"[{kind}] {l}")
    print(f"\nreport: {out / 'REPORT.md'}")


if __name__ == "__main__":
    main()
