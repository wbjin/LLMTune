# PostgreSQL perf comparison

Server process tree, measured pgbench invocation only. TPS includes profiling overhead.
Perf interval ratios/rates: median excluding the first interval, then median across runs.
Hardware and syscall counts/txn: all intervals, only when every interval counted the event.
PostgreSQL counters: snapshot deltas per completed pgbench transaction.
A dash means the metric was unavailable in at least one run.

| Metric | Default | Optimized |
|---|---:|---:|
| TPS | 1,101 | 1,504 |
| latency_ms | 14.48 | 10.59 |
| IPC | 0.2311 | 0.2412 |
| blks_hit/txn | 27.31 | 26.84 |
| blks_read/txn | 0.2979 | 0.01393 |
| branch-misses / 1k instr | 20.3 | 19.73 |
| branch-misses/txn | 1.735e+04 | 1.747e+04 |
| buffers_checkpoint/txn | 0 | 0 |
| cache miss % | 4.121 | 3.926 |
| cache-misses / 1k instr | 20.92 | 19.99 |
| cache-misses/txn | 1.792e+04 | 1.779e+04 |
| cache-references / 1k instr | 507.9 | 509.2 |
| context-switches / s | 1.058e+04 | 1.624e+04 |
| cpu-migrations / s | 3,738 | 5,345 |
| cycles/txn | 3.702e+06 | 3.687e+06 |
| instructions/txn | 8.716e+05 | 8.939e+05 |
| page-faults / s | 3,472 | 7,300 |
| syscalls:sys_enter_fdatasync / s | 275.1 | 451.3 |
| syscalls:sys_enter_fdatasync/txn | 0.2964 | 0.2987 |
| syscalls:sys_enter_fsync / s | 0 | 0 |
| syscalls:sys_enter_fsync/txn | 0 | 0 |
| syscalls:sys_enter_pread64 / s | 217.4 | 0 |
| syscalls:sys_enter_pread64/txn | 0.2877 | 0.005317 |
| syscalls:sys_enter_preadv / s | 0 | 0 |
| syscalls:sys_enter_preadv/txn | 0 | 0 |
| syscalls:sys_enter_pwrite64 / s | 466.8 | 457.7 |
| syscalls:sys_enter_pwrite64/txn | 0.4383 | 0.309 |
| syscalls:sys_enter_pwritev / s | 0 | 0 |
| syscalls:sys_enter_pwritev/txn | 0 | 0 |
| wal_bytes/txn | 480.1 | 469.7 |
| wal_fpi/txn | 0.0001513 | 0.000155 |

## Interpretation

The optimized configuration achieved **1,504 TPS versus 1,101 TPS (+36.6%)**.
Mean transaction latency fell from **14.48 to 10.59 ms (−26.9%)**. With 16
clients each issuing its next transaction after the last one finished, that
latency reduction accounts for the throughput ratio: 14.48 / 10.59 ≈ 1.37×.
Both pairs showed the gain, including the pair with optimized settings first.

| Round | Default TPS | Optimized TPS | Default backend buffer writes | Optimized backend buffer writes |
|---|---:|---:|---:|---:|
| 1 (default first) | 1,105 | 1,501 | 4,936 | 0 |
| 2 (optimized first) | 1,096 | 1,507 | 4,808 | 0 |

### What the syscall measurements add

- **Reads:** Per 1,000 transactions, PostgreSQL blocks read fell from 298 to 14
  (−95%), and server `pread64` calls fell from 288 to 5 (−98%). Both the database
  and kernel counters point to fewer page fetches with the larger buffer pool.
  The optimized median of zero reads per second does not mean no reads occurred:
  the per-second metric is a median of intervals; the per-transaction metric
  includes the whole invocation. A read syscall can be served by the OS cache.
- **Writes:** `pwrite64` fell from 438 to 309 calls per 1,000 transactions
  (−30%). The median write rate per second stayed close (467 versus 458), but the
  optimized server completed more transactions in that time. The per-round
  snapshots above show that direct backend writes of PostgreSQL buffers disappeared.
  The remaining write calls are server-wide and include other work such as WAL;
  these tracepoints do not identify files or count bytes.
- **Durable flushes:** `fdatasync` stayed near 0.30 calls per transaction
  (0.2964 versus 0.2987); `fsync` was zero in both conditions. The optimized
  server issued more flushes per second, but there is no evidence it gained
  throughput by skipping flushes per transaction. Call counts do not measure
  time blocked in a flush.

### Most likely explanation and limits

The strongest explanation is that increasing `shared_buffers` from 128 MB to
16 GB let PostgreSQL keep more active pages resident and reuse them without
first writing modified pages to make room. This reduced work on the transaction
path. CPU cycles per transaction fell only 0.4% (3.702M to 3.687M), far less
than elapsed latency. The CPU cache miss rate also changed little (4.12% to
3.93%); it measures a different cache from PostgreSQL's buffer pool. Higher
context switches or page faults per second do not establish a bottleneck or
swapping.

WAL compression was also enabled, but WAL bytes per transaction fell only
2.2% (480.1 to 469.7) and full-page images were rare in both cases.
`max_wal_size` stayed at 1 GB. Changing `commit_siblings` had no effect with
`commit_delay=0`. These results favor buffer sizing as the explanation, but
they do not isolate its contribution or measure physical disk waits directly.

Neither configuration recorded a checkpoint in any of the four 30-second
windows. The larger buffer pool may have deferred dirty-page writes until after
measurement. To test whether the gain persists, compare the default with **16 GB
shared buffers and WAL compression off**, alternating order across runs long
enough to include several checkpoints. Collect block I/O and WAL sync timings
under identical instrumentation to distinguish fewer operations from less time
spent waiting.

Sources: [per-run metrics](runs.csv), [configurations and protocol](protocol.json),
and each round's `repetition-1.json` before/after PostgreSQL statistics.
