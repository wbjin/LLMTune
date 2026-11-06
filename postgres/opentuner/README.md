# OpenTuner integration for PostgreSQL

## Purpose

Use OpenTuner to search PostgreSQL configurations for a fixed workload on
node0. Compare its results and tuning cost with the default configuration, the
manually designed search, and the agentic optimizer.

## Status

The adapter searches configurations on the wbjin-owned PostgreSQL instance.
It supports optional manual seeding and independent alternating validation.

## Expanded search result on node0 (2026-11-02)

Trial 6 had the highest observed throughput: **1,607 TPS** across 34 measured
configurations in `opentuner-results-20261102-192337`. Each trial ran pgbench
at scale 10 with a 5-second warm-up and one 30-second measurement. There was
no independent validation in this run, so this is a search result, not a
verified speedup over the default or hand-tuned configuration.

| Setting | PostgreSQL 14 default | node0 before tuning | Trial 6 |
|---|---:|---:|---:|
| `shared_buffers` | Typically 128 MB | 128 MB | 16 GB |
| `max_wal_size` | 1 GB | 1 GB | 1 GB |
| `wal_compression` | off | off | on |
| `commit_delay` | 0 µs | 0 µs | 0 µs |
| `commit_siblings` | 5 | 5 | 10 |

Best observed configuration (trial 6): **1,607.00 TPS** in one 30-second measurement.

```conf
shared_buffers = 16GB
max_wal_size = 1GB
wal_compression = on
commit_delay = 0
commit_siblings = 10
```

Because `commit_delay` was zero, changing `commit_siblings` did not introduce
a commit delay. The effective changes from the baseline were the larger buffer
pool and WAL compression; this trial does not isolate their individual effects.

Defaults: [PostgreSQL 14 memory settings](https://www.postgresql.org/docs/14/runtime-config-resource.html)
and [WAL settings](https://www.postgresql.org/docs/14/runtime-config-wal.html).
The node0 values come from `initial-settings.json`; the winning settings and
score are in `best-config.json` and `trial-0006/result.json` in the run directory.

## Environment

- User-owned PostgreSQL 14 instance on port `5432` (`/users/wbjin/pgdata`).
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

## Improving the search and comparing against manual tuning

From the `postgres/opentuner` directory, after syncing project dependencies:

```bash
uv run tune_postgres.py --fast --test-limit=24 \
  --manual-summary ../hand-tuned/handtuned-results-20261102-182232/summary.json \
  --validate --validation-duration=60 --validation-rounds=3
```

Run as `wbjin`, without sudo. This preserves scale 10, 30-second search
measurements, five-second warm-ups, 16 clients, and four benchmark workers.
Every measurement rebuilds the dataset. Durability settings remain enabled.
The budget is search requests, not guaranteed distinct configurations; duplicate
adapter requests reuse the first score to avoid wasting database measurements.
OpenTuner can also deduplicate requests internally. Search counts and elapsed
time are recorded separately from validation.

The manual selection is measured afresh as a seed, not assigned its historical
score. This experiment is explicitly **manual-seeded OpenTuner refinement**,
not an independent optimizer-versus-human comparison. Omit `--manual-summary`
and `--validate` to run an unseeded search. Keep budgets equal when comparing
search methods; 24 requests provide more opportunity than the previous six.

Validation bypasses the search cache and alternates manual/selected then
selected/manual order across rounds. Both configurations use the same dataset,
warm-up, duration, and repetition count. `validation-summary.json` records all
samples, median TPS, and the selected/manual ratio. If the configurations match,
any measured difference is noise, not a tuning gain. One short run per candidate
can mis-rank candidates; increase `--repeats` and validation duration for final
claims. A throughput increase is not guaranteed, especially when candidates
already perform within about one percent of each other.

Outputs include `manual-seed.json`, `best-config.json`, `search-counts.json`,
`search-summary.json`, and (with `--validate`) `validation-summary.json`.
Existing plots still show search trials; validation results are separate.

## Expanded search space

The default `--search-space=original` preserves the previous 48 combinations.
Use this unseeded search to enable 768 combinations across five parameters:

```bash
uv run tune_postgres.py --fast --search-space=expanded \
  --test-limit=48 --search-seed=1
```

| Parameter | Expanded values |
|---|---|
| shared_buffers | 128MB, 256MB, 512MB, 1GB, 2GB, 4GB, 8GB, 16GB |
| max_wal_size | 1GB, 2GB, 4GB, 8GB |
| wal_compression | off, on |
| commit_delay | 0, 10, 50, 100 microseconds |
| commit_siblings | 1, 5, 10 concurrent transactions |

Commit delay can allow transactions to share a WAL flush, trading additional
latency for potentially higher throughput. Commit siblings controls the minimum
concurrent transaction count for applying that delay. With zero delay, the
siblings setting has no effect. These knobs preserve synchronous commits,
fsync, and full-page writes; no workload parameters are added to the search.
See [PostgreSQL 14 WAL settings](https://www.postgresql.org/docs/14/runtime-config-wal.html).

The budget remains search requests, not exhaustive coverage or guaranteed unique
measurements. More dimensions require more exploration and may not improve TPS.
Compare expanded-space results separately from the original-space manual baseline.
Use equal measurement settings and independent validation for performance claims.
The chosen space is saved in `search-space.json`; plots discover all trial knobs
and only compare parameters when every other searched setting matches.
With five knobs, matched comparisons may be sparse.

For manual-seeded expanded searches, missing new knobs are recovered from the
manual run's `selected-validation-settings.json`. Missing or out-of-space values
raise an error; no historical settings are guessed. Existing original-space runs
and plots remain supported. All searched settings are checked after restoration.
