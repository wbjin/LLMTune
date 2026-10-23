# OpenTuner integration for PostgreSQL

## Purpose

Use OpenTuner to search PostgreSQL configurations for a fixed workload on
node0. Compare its results and tuning cost with the default configuration, the
manually designed search, and the agentic optimizer.

## Status

Integration planning only. OpenTuner is not installed and no PostgreSQL tuning
adapter has been implemented yet.

## Environment

- PostgreSQL `14/main` on port `5432`.
- 8 ARM CPUs, 62 GiB RAM, non-rotational storage, 64 GiB root partition.
- Project Python version: `3.10`.
- pgbench scale `100`; observed database size approximately 1627 MB.
- Fixed workload: TPC-B-like read/write, simple query mode, 16 clients, 4
pgbench worker threads.

## Installation approach

The linked setup tutorial contains legacy Python 2 instructions. Check the
upstream source and dependency compatibility with Python 3.10. Use an isolated
uv environment; verify an import and a small upstream example before running
database experiments. Pin the working OpenTuner version or commit and its
dependencies for reproducibility.

## Initial search space

- `shared_buffers`: `128MB`, `1GB`, `2GB`, `4GB`, `8GB`, `16GB`
- `max_wal_size`: `1GB`, `2GB`, `4GB`, `8GB`
- `wal_compression`: `off`, `on`

This gives 48 combinations and includes the baseline and manual winner. Search
combinations jointly: buffer size and WAL allowance can interact. Keep `fsync`,
`synchronous_commit`, `full_page_writes`, and `autovacuum` enabled. Keep
`checkpoint_timeout` and other settings fixed.

## Measurement adapter

Implement an OpenTuner `MeasurementInterface`:

- `manipulator()`: declare the discrete configuration parameters.
- `run()`: apply a candidate, restart PostgreSQL, verify effective settings,
prepare a dedicated benchmark database, warm up, and measure pgbench.
- Return `Result(time=1.0 / median_tps)` for successful positive-throughput
measurements so minimizing the objective maximizes throughput.
- Record failures separately and never treat a failed run as a fast result.
- `save_final_config()`: export the selected settings for later validation.

Run only one database evaluation at a time. Prevent overlapping tuner runs.
Back up configuration before changes and restore it on exit or failure. Provide
recovery instructions for interruptions that cannot be caught. Use bounded
subprocess timeouts and retain error output.

## Benchmark protocol and comparison

Use the same dataset preparation, warm-up, measurement duration, repetitions,
and monitoring for each optimizer. Keep initialization outside timing. Save raw
output, TPS, latency, settings, and before/after PostgreSQL counters. Record
the search seed, evaluated configurations, and total elapsed time. Set an
explicit evaluation or wall-clock budget for fair comparisons.

Validate the selected configuration using independent longer runs. Alternate
baseline and selected configurations to reduce run-order effects. Report median
throughput, variability, speedup, and total tuning cost.

The manual validation achieved 401.2 TPS baseline versus 1144.0 TPS selected,
with `shared_buffers=4GB`, `max_wal_size=4GB`, and `wal_compression=off`. These
are historical reference measurements, not guaranteed future results.

## Implementation notes

The existing `hand-tuned/test_strategy.py` executes work at module import. Do
not import it directly as a reusable benchmark library. Extract shared
measurement code behind a main guard before sharing it.

Keep this directory free of an `__init__.py` to avoid making it a local Python
package that could shadow the installed `opentuner` package.

## References

- [OpenTuner setup tutorial](https://opentuner.org/tutorial/setup/)
- [OpenTuner integration
tutorial](https://opentuner.org/tutorial/gettingstarted/)
- [OpenTuner source repository](https://github.com/jansel/opentuner)
