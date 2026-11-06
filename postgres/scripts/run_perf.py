"""Profile database-server work during matched default and optimized pgbench runs."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
from statistics import median
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "postgres/opentuner"))
from postgres_benchmark import PostgresBenchmark, EXPANDED_SPACE, PG_DATA, positive
from perf_intervals import EVENTS, parse_intervals, time_series

IO_TRACEPOINTS = tuple("syscalls:sys_enter_" + call for call in
                       ("pread64", "pwrite64", "preadv", "pwritev", "fdatasync", "fsync"))
POSTGRES_EVENTS = ",".join((EVENTS, *IO_TRACEPOINTS))

DEFAULT = dict(shared_buffers="128MB", max_wal_size="1GB", wal_compression="off",
               commit_delay="0", commit_siblings="5")
WINNER = ROOT / "postgres/opentuner/opentuner-results-20261102-192337/best-config.json"
PLOT = Path(__file__).with_name("plot_perf_stat.py")


def server_pids():
    """Attach to the postmaster and already-running PostgreSQL workers.
    perf inheritance covers new backends spawned for the measured connections.
    """
    parent = int((PG_DATA / "postmaster.pid").read_text().splitlines()[0])
    proc = Path("/proc") / str(parent)
    if proc.stat().st_uid != os.geteuid() or (proc / "exe").resolve().name != "postgres":
        raise RuntimeError("postmaster.pid does not identify this user's postgres process")
    children = (proc / "task" / str(parent) / "children").read_text().split()
    # The preceding SQL snapshot can leave a backend in the process of
    # exiting. Passing its PID races perf's thread enumeration. Attach only
    # to persistent PG14 workers; future client backends inherit the parent.
    stable = {"checkpointer", "background writer", "walwriter", "autovacuum launcher",
              "stats collector", "logical replication launcher"}
    targets = [str(parent)]
    for pid in children:
        try:
            title = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0", 1)[0]
        except (FileNotFoundError, ProcessLookupError):
            continue
        if title.decode(errors="replace").strip() in {"postgres: " + role for role in stable}:
            targets.append(pid)
    return ",".join(targets)


def perf_argv(args, pids, output, workload):
    # The timing command is pgbench, but the counter target is the server PID list.
    # With sudo, immediately drop privileges for the client; never run the harness as root.
    prefix = ["sudo", "-n", "--"] if args.sudo_perf else []
    client = ["/usr/sbin/runuser", "-u", "wbjin", "--"] if args.sudo_perf else []
    return [*prefix, "perf", "stat", "--no-big-num", "-x", ",", "-I", str(args.interval),
            "-e", args.events, "-p", pids, "-o", str(output), "--", *client, *workload]


class Profile(PostgresBenchmark):
    database = "pgbench_perf"

    def measure(self, argv, timeout, trial, repetition):
        raw = trial / "perf_stat.csv"
        raw.touch(mode=0o600)  # File remains owned by wbjin with --sudo-perf.
        argv = ["/usr/lib/postgresql/14/bin/pgbench", *argv[1:]]
        cmd = perf_argv(self.args, server_pids(), raw, argv)
        output = self.command(cmd, timeout=timeout)
        (trial / "pgbench.log").write_text(output + "\n")
        return output


def summarize(trial, measurement):
    raw = trial / "perf_stat.csv"
    intervals = parse_intervals(raw)
    series = time_series(intervals)
    fields = sorted({k for _, values in series for k in values})
    if not fields:
        raise RuntimeError(f"No usable interval metrics in {raw}; check events and permissions")
    with (trial / "intervals.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, ["seconds", *fields])
        writer.writeheader()
        for t, values in series:
            writer.writerow(dict(seconds=t, **values))
    values = {key: median(v[key] for _, v in series if key in v) for key in fields}
    tx = int(re.search(r"^number of transactions actually processed:\s*(\d+)",
                       (trial / "pgbench.log").read_text(), re.M)[1])
    values.update(TPS=measurement["tps"], latency_ms=measurement["latency_ms"])
    # Use the whole measured invocation for counts per transaction, and report
    # only events that were counted throughout (never substitute missing zeros).
    for event in ("cycles", "instructions", "cache-misses", "branch-misses", *IO_TRACEPOINTS):
        if tx and intervals and all(event in ev for _, ev in intervals):
            values[event + "/txn"] = sum(ev[event] for _, ev in intervals) / tx
    before, after = measurement["before"], measurement["after"]
    for view, key in (("wal", "wal_bytes"), ("wal", "wal_fpi"),
                      ("database", "blks_read"), ("database", "blks_hit"),
                      ("bgwriter", "buffers_checkpoint")):
        if before[view]["stats_reset"] == after[view]["stats_reset"] and tx:
            values[key + "/txn"] = (int(after[view][key]) - int(before[view][key])) / tx
    return values


def report(out, rows):
    grouped = {name: [m for label, m in rows if label == name] for name in ("default", "optimized")}
    lines = ["# PostgreSQL perf comparison", "",
             "Server process tree, measured pgbench invocation only. TPS includes profiling overhead.",
             "Perf interval ratios/rates: median excluding the first interval, then median across runs.",
             "Hardware and syscall counts/txn: all intervals, only when every interval counted the event.",
             "PostgreSQL counters: snapshot deltas per completed pgbench transaction.",
             "A dash means the metric was unavailable in at least one run.", "",
             "| Metric | Default | Optimized |", "|---|---:|---:|"]
    keys = ["TPS", "latency_ms", *sorted(set().union(*(m for _, m in rows)) - {"TPS", "latency_ms"})]
    with (out / "runs.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, ["configuration", *keys])
        writer.writeheader()
        for label, values in rows:
            writer.writerow(dict(configuration=label, **values))
    for key in keys:
        cells = [f"{median(m[key] for m in group):,.4g}" if all(key in m for m in group) else "—"
                 for group in grouped.values()]
        lines.append(f"| {key} | {' | '.join(cells)} |")
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=WINNER, help="OpenTuner best-config.json")
    for name, default in (("duration", 30), ("warmup", 5), ("scale", 10), ("rounds", 2),
                          ("interval", 1000)):
        parser.add_argument("--" + name, type=positive, default=default)
    parser.add_argument("--events", default=POSTGRES_EVENTS, help="Override the default CPU, software and PostgreSQL I/O syscall counters")
    parser.add_argument("--sudo-perf", action="store_true", help="Use sudo -n only for perf")
    parser.add_argument("--output", type=Path, default=None, help="New results directory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.duration * 1000 < 2 * args.interval:
        parser.error("Allow at least two perf intervals per measurement")
    try:
        optimized = json.loads(args.config.read_text())
        if set(optimized) != set(DEFAULT) or any(optimized[k] not in EXPANDED_SPACE[k] for k in DEFAULT):
            parser.error("Expected an expanded-space PostgreSQL best-config.json")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    configs = {"default": DEFAULT, "optimized": optimized}
    print(json.dumps(configs, indent=2))
    print(f"Scale {args.scale}, 16 clients, 4 threads, {args.warmup}s warm-up, "
          f"{args.duration}s measurements, {args.rounds} paired round(s)", flush=True)
    if args.dry_run:
        print(shlex.join(perf_argv(args, "SERVER_PIDS", "perf_stat.csv",
                                   ["pgbench", "-c", "16", "-j", "4", "-T", str(args.duration), "pgbench_perf"])))
        return
    if os.geteuid() == 0:
        parser.error("Run as wbjin; use --sudo-perf if needed")
    if not shutil.which("perf"):
        parser.error("Install the Linux perf tool matching the node's kernel")
    # Fail before restarting PostgreSQL if the node denies attaching to it.
    probe = perf_argv(args, server_pids(), "/dev/null", ["/bin/true"])
    # Keep diagnostics on stderr in the probe rather than discarding them.
    del probe[probe.index("-o"):probe.index("-o") + 2]
    probe_result = subprocess.run(probe, capture_output=True, text=True, timeout=15)
    if probe_result.returncode:
        parser.error(f"Server perf attach failed: {probe_result.stderr.strip()}\n"
                     "If authorized, run sudo -v then retry with --sudo-perf.")
    import matplotlib  # Check plotting dependency before the first server restart.
    out = args.output or Path(time.strftime("pgperf-results-%Y%m%d-%H%M%S"))
    bench = Profile(out, args.duration, args.warmup, 1, args.scale, search_space="expanded")
    bench.args = args
    def terminate(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, terminate)
    rows = []
    with bench.session():
        bench.save(bench.output / "protocol.json", {
            **vars(args), "config": str(args.config), "output": str(bench.output),
            "configurations": configs, "scope": "postmaster, workers and inherited descendants",
            "perf_version": bench.command(["perf", "--version"]),
        })
        for i in range(args.rounds):
            for name in (("default", "optimized") if i % 2 == 0 else ("optimized", "default")):
                label = f"round-{i+1:02d}-{name}"
                print(f"Profiling {label}", flush=True)
                result = bench.evaluate(configs[name], label)
                rows.append((name, summarize(bench.output / label, result["measurements"][0])))
    # Restore the database before rendering. Raw data remains useful even if plotting fails.
    report(bench.output, rows)
    for i in range(1, args.rounds + 1):
        prefix = f"round-{i:02d}"
        subprocess.run([sys.executable, str(PLOT), str(bench.output / f"{prefix}-default/perf_stat.csv"),
                        str(bench.output / f"{prefix}-comparison.png"), "--compare",
                        str(bench.output / f"{prefix}-optimized/perf_stat.csv")], check=True)
    print(bench.output / "REPORT.md")


if __name__ == "__main__":
    main()
