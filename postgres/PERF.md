# Profile PostgreSQL on node0

From the LLMTune repository root, preview the paired run (no database changes):

    cd postgres
    uv run --with matplotlib python scripts/run_perf.py --dry-run

Run it as wbjin with the user-owned PostgreSQL 14 instance already online. The
comparison uses the recorded expanded-space winner from trial 6; pass
`--config /path/to/best-config.json` if that result directory is not present.
Only the five search knobs are changed. The baseline explicitly sets their
PostgreSQL 14 defaults; all other settings are shared across the two conditions.

    uv run --with matplotlib python scripts/run_perf.py

If the attach probe reports insufficient permissions (node0 was observed at
perf_event_paranoid=4), use an authorized perf capability or:

    sudo -v
    uv run --with matplotlib python scripts/run_perf.py --sudo-perf

Do not sudo the Python script. In this mode only the perf wrapper elevates;
its timed pgbench child drops back to wbjin. The script does not change sysctls.
Do not run other workloads or tuners against this instance during collection.
Each condition restarts it and rebuilds the benchmark tables in pgbench_perf.
The OpenTuner lock, configuration backup, recovery instructions and restore
procedure are reused.

Defaults: scale 10, 16 clients, 4 threads, simple read/write transactions,
5 s warm-up, 30 s measurement, 1000 ms counter intervals and two rounds
(default/optimized, then optimized/default). Change --rounds, --duration,
--warmup, --scale, --interval (ms), or --events explicitly. On hardware with
few PMU counters, repeat with smaller event sets, e.g. --events cycles,instructions.
Use the same events and protocol for both conditions.

perf stat attaches to the postmaster and its existing children immediately
before the measured pgbench invocation. New client backends inherit counters.
The separate pgbench client is not a counter target. Initialization, warm-up
and restart are excluded; background work by this instance is included.
PG14 pgbench's standard pre-run maintenance and connection setup can fall in
the perf window; pgbench reports TPS excluding initial connection time.

Each new pgperf-results-* directory contains REPORT.md, protocol.json,
per-round comparison plots, and per-condition raw perf_stat.csv, intervals.csv,
pgbench.log, effective settings and pg_stat snapshots. Missing/unsupported
events are not treated as zero. Inspect raw perf CSV running percentages for
multiplexing. Summary CPU metrics drop the first interval; PostgreSQL deltas
span the full measured invocation. Compare WAL bytes and full-page images per
transaction alongside CPU IPC, cache-miss rate, buffer reads/hits and TPS.
CPU cache misses are not PostgreSQL buffer misses. WAL and checkpoint counters
are instance-wide. These metrics suggest mechanisms; the two changed effective
settings (buffers and WAL compression) require separate trials to isolate causality.

To regenerate an overlay without connecting to PostgreSQL, from the repo root:

    python postgres/scripts/plot_perf_stat.py DEFAULT/perf_stat.csv comparison.png --compare OPTIMIZED/perf_stat.csv

The original single-input plot command and AI workload stat/record commands remain available.
