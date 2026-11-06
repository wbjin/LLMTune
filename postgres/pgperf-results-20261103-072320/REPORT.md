# PostgreSQL perf comparison

Server process tree, measured pgbench invocation only. TPS includes profiling overhead.
Perf interval ratios/rates: median excluding the first interval, then median across runs.
Hardware counts/txn: all intervals, only when every interval counted the event.
PostgreSQL counters: snapshot deltas per completed pgbench transaction.
A dash means the metric was unavailable in at least one run.

| Metric | Default | Optimized |
|---|---:|---:|
| TPS | 1,110 | 1,527 |
| latency_ms | 14.36 | 10.43 |
| IPC | 0.229 | 0.2394 |
| blks_hit/txn | 25.97 | 28.45 |
| blks_read/txn | 0.295 | 0.01407 |
| branch-misses / 1k instr | 20.68 | 19.98 |
| branch-misses/txn | 1.705e+04 | 1.702e+04 |
| buffers_checkpoint/txn | 0 | 0 |
| cache miss % | 4.041 | 3.871 |
| cache-misses / 1k instr | 20.53 | 19.54 |
| cache-misses/txn | 1.693e+04 | 1.675e+04 |
| cache-references / 1k instr | 508.4 | 504.7 |
| context-switches / s | 1.039e+04 | 1.639e+04 |
| cpu-migrations / s | 3,715 | 5,380 |
| cycles/txn | 3.606e+06 | 3.577e+06 |
| instructions/txn | 8.29e+05 | 8.562e+05 |
| page-faults / s | 3,540 | 7,479 |
| wal_bytes/txn | 479.6 | 469.2 |
| wal_fpi/txn | 0.0002535 | 0.0001527 |

## Interpretation

The optimized configuration increased throughput from **1,110 to 1,527 TPS
(+37.6%)** and reduced mean transaction latency from **14.36 to 10.43 ms**.
It was faster in both rounds, including the round in which it ran first.
With 16 concurrent clients, each client starts its next transaction when the
previous one finishes. The latency ratio (14.36 / 10.43 = 1.38) closely matches
the observed 1.38× throughput. See [per-run measurements](runs.csv).

**The strongest evidence points to less page replacement in PostgreSQL's shared
buffer pool.** Increasing `shared_buffers` from 128 MB to 16 GB coincided with a
95% drop in blocks read per transaction (0.295 to 0.0141). These are reads into
PostgreSQL's buffers; the OS cache may satisfy them without physical disk I/O.
The before/after snapshots also show work disappearing from the transaction path:

| Buffers written directly by backends during measurement | Default | Optimized |
|---|---:|---:|
| Round 1 | 4,831 | 0 |
| Round 2 | 4,799 | 0 |

With the smaller pool, backends sometimes had to write modified pages to free
buffers for reuse. The larger pool could retain those pages. CPU cycles per
transaction fell by only 0.8%, despite the 27% latency reduction: transactions
finished sooner without a comparable reduction in CPU work. This is consistent
with less time waiting on the buffer and I/O path, though wait time was not
measured directly.

**The evidence for other explanations is weaker.** WAL compression was enabled,
but WAL bytes per transaction fell only 2.2% (479.6 to 469.2). CPU cache miss
rate changed from 4.04% to 3.87%; this measures a different cache from the
PostgreSQL buffer pool. Changing `commit_siblings` from 5 to 10 had no effect
because `commit_delay` remained zero. `max_wal_size` stayed at 1 GB.

**Limit and follow-up:** no checkpoints occurred in any of the four 30-second
measurement windows. Zero backend writes during those windows means writes may
have been deferred, not eliminated. To isolate the buffer effect, compare the
default against `shared_buffers=16GB` with `wal_compression=off` and all other
settings held fixed. Repeat over multiple checkpoint cycles to test whether the
throughput gain persists.
