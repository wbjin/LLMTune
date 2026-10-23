#!/usr/bin/env python3
"""Test the node0 PostgreSQL 14 tuning strategy; requires root."""

import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import time


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--duration", type=positive, default=600)
parser.add_argument("--warmup", type=positive, default=120)
parser.add_argument("--repeats", type=positive, default=3)
parser.add_argument("--scale", type=positive, default=100)
args = parser.parse_args()

if os.geteuid() != 0:
    parser.error("run with sudo python3")

# Avoid inherited connection settings changing the target or workload.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
ENV["LC_ALL"] = "C"
CONNECTION = ["-h", "/var/run/postgresql", "-p", "5432"]
DB = "pgbench_handtuned"
KNOBS = ("shared_buffers", "max_wal_size", "wal_compression")
out = Path.cwd() / time.strftime("handtuned-results-%Y%m%d-%H%M%S")
out.mkdir(mode=0o700)


def run(command, postgres=False):
    if postgres:
        command = ["runuser", "-u", "postgres", "--", *command]
    return subprocess.check_output(
        command, text=True, stderr=subprocess.STDOUT, env=ENV, cwd="/tmp"
    )


def sql(query):
    return run(
        ["psql", *CONNECTION, "-X", "-At", "-v", "ON_ERROR_STOP=1",
         "-d", "postgres", "-c", query],
        postgres=True,
    ).strip()


def save(name, text):
    (out / name).write_text(text + "\n")


def restart():
    run(["pg_ctlcluster", "14", "main", "restart"])
    run(["pg_isready", *CONNECTION], postgres=True)


def settings():
    return json.loads(sql(
        "SELECT json_object_agg(name, current_setting(name)) "
        "FROM pg_settings"
    ))


def snapshot():
    return sql("""
        SELECT json_build_object(
          'bgwriter', (SELECT row_to_json(s) FROM pg_stat_bgwriter s),
          'wal', (SELECT row_to_json(s) FROM pg_stat_wal s),
          'database', (SELECT row_to_json(s) FROM pg_stat_database s
                       WHERE datname = 'pgbench_handtuned'));
    """)


initial = settings()
if not initial["server_version"].startswith("14."):
    raise RuntimeError("Expected PostgreSQL 14")
if initial["data_directory"] != "/var/lib/postgresql/14/main":
    raise RuntimeError("Port 5432 is not the expected 14/main cluster")
for key in ("fsync", "synchronous_commit", "full_page_writes", "autovacuum"):
    if initial[key] != "on":
        raise RuntimeError(f"Expected {key}=on; found {initial[key]}")
if sql("SELECT EXISTS (SELECT 1 FROM pg_settings WHERE pending_restart)") == "t":
    raise RuntimeError("Resolve pending configuration restarts before testing")

data = Path(initial["data_directory"])
wal = (data / "pg_wal").resolve()
for location in (data, wal):
    if shutil.disk_usage(location).free < 20 * 1024**3:
        raise RuntimeError(f"Need at least 20 GiB free on {location}")

save("initial-settings.json", json.dumps(initial, indent=2))
save("hardware.txt", run(["lscpu"]) + run(["free", "-h"])
     + run(["df", "-h", str(data), str(wal)]))
auto = data / "postgresql.auto.conf"
backup = out / "postgresql.auto.conf.original"
had_auto = auto.exists()
if had_auto:
    shutil.copy2(auto, backup)
save("recovery.txt",
     f"Original auto.conf existed: {had_auto}\n"
     f"If interrupted by SIGKILL or power loss, restore {auto} from "
     f"{backup} (or remove auto.conf if originally absent), then run:\n"
     "sudo pg_ctlcluster 14 main restart\n"
     "Do not edit PostgreSQL configuration concurrently with this test.")


def apply(config):
    for name, value in config.items():
        escaped = value.replace("'", "''")
        sql(f"ALTER SYSTEM SET {name} = '{escaped}'")
    # Restart every candidate consistently, even for reloadable settings.
    restart()
    effective = settings()
    for name, value in config.items():
        if effective[name] != value:
            raise RuntimeError(f"{name}: requested {value}, got {effective[name]}")


def benchmark(label, config, duration):
    print(f"Testing {label}: {config}", flush=True)
    apply(config)
    save(f"{label}-settings.json", json.dumps(settings(), indent=2))
    values = []
    for repetition in range(1, args.repeats + 1):
        prefix = f"{label}-{repetition}"
        if shutil.disk_usage(data).free < 10 * 1024**3:
            raise RuntimeError("Less than 10 GiB free before repetition")
        save(f"{prefix}-init.log", run(
            ["pgbench", *CONNECTION, "-i", "-s", str(args.scale), DB], True))
        command = ["pgbench", *CONNECTION, "-c", "16", "-j", "4",
                   "-M", "simple", "-P", "5"]
        save(f"{prefix}-warmup.log", run(
            [*command, "-T", str(args.warmup), DB], True))
        save(f"{prefix}-before.json", snapshot())
        output = run([*command, "-T", str(duration), DB], True)
        save(f"{prefix}-benchmark.log", output)
        save(f"{prefix}-after.json", snapshot())
        tps = float(re.search(r"^tps = ([\d.]+)", output, re.M)[1])
        latency = float(re.search(
            r"^latency average = ([\d.]+)", output, re.M)[1])
        values.append(tps)
        with (out / "runs.csv").open("a") as handle:
            csv.writer(handle).writerow([label, repetition, tps, latency])
        print(f"  repetition {repetition}: {tps:.1f} TPS", flush=True)
    return {"median": statistics.median(values),
            "range": max(values) - min(values), "runs": values}


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"Received signal {signum}")


signal.signal(signal.SIGTERM, interrupted)
baseline = {name: initial[name] for name in KNOBS}
save("runs.csv", "configuration,repetition,tps,latency_ms")
started = time.monotonic()

try:
    if sql(f"SELECT 1 FROM pg_database WHERE datname = '{DB}'") != "1":
        run(["createdb", *CONNECTION, DB], True)
    best = baseline.copy()
    best_result = benchmark("baseline", best, args.duration)
    decisions = []
    candidates = [
        ("shared_buffers", "4GB"), ("shared_buffers", "8GB"),
        ("shared_buffers", "16GB"), ("max_wal_size", "4GB"),
        ("max_wal_size", "8GB"), ("wal_compression", "on"),
    ]
    for index, (name, value) in enumerate(candidates, 1):
        candidate = {**best, name: value}
        result = benchmark(f"candidate-{index}", candidate, args.duration)
        threshold = max(best_result["median"] * 0.05,
                        best_result["range"], result["range"])
        accepted = result["median"] > best_result["median"] + threshold
        decisions.append({"config": candidate, "result": result,
                          "accepted": accepted, "threshold_tps": threshold})
        if accepted:
            best, best_result = candidate, result
        save("decisions.json", json.dumps(decisions, indent=2))
    validation_seconds = max(1800, args.duration)
    control = benchmark("baseline-validation", baseline, validation_seconds)
    final = benchmark("selected-validation", best, validation_seconds)
    save("summary.json", json.dumps({
        "selected": best,
        "baseline_validation": control,
        "selected_validation": final,
        "speedup": final["median"] / control["median"],
        "elapsed_seconds": time.monotonic() - started,
        "note": "Selection threshold is a heuristic, not a significance test."
    }, indent=2))
finally:
    # Restore the original file, including any pre-existing ALTER SYSTEM values.
    if had_auto:
        shutil.copy2(backup, auto)
    else:
        auto.unlink(missing_ok=True)
    restart()
    print(f"Original configuration restored. Results: {out}", flush=True)
