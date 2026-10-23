# 592 Project

## Install postgres with
```bash
bash scripts/install_postgres.sh
```

## Benchmarking
```bash
bash scripts/start_postgres.sh
bash scripts/run_pgbench.sh
```
Benchmark characteristics:
- **`CLIENTS` (default: 16):** Number of concurrent database connections
simulating clients that execute transactions.
- **`THREADS` (default: 4):** Number of pgbench worker threads driving those
clients. These are benchmark-generator threads, not PostgreSQL server threads.
- **`DURATION` (default: 60):** Benchmark runtime in seconds, excluding
database initialization.
- **`SCALE` (default: 100):** Initial dataset size multiplier. Each unit
creates 100,000 account rows, so `100` creates 10 million. Rerun with `SCALE=X
./run_pgbench.sh --clean` to change the number of rows

This benchmark measures PostgreSQL’s transaction throughput and latency under a
concurrent read/write workload with configurable dataset size, client count,
worker threads, and duration.

Results:
```bash
/var/run/postgresql:5432 - accepting connections
dropping old tables...
NOTICE:  table "pgbench_accounts" does not exist, skipping
NOTICE:  table "pgbench_branches" does not exist, skipping
NOTICE:  table "pgbench_history" does not exist, skipping
NOTICE:  table "pgbench_tellers" does not exist, skipping
creating tables...
generating data (client-side)...
10000000 of 10000000 tuples (100%) done (elapsed 69.72 s, remaining 0.00 s)
vacuuming...
creating primary keys...
done in 88.72 s (drop tables 0.00 s, create tables 0.02 s, client-side generate 69.98 s, vacuum 5.96 s, primary keys 12.77 s).
pgbench (14.24 (Ubuntu 14.24-0ubuntu0.22.04.1))
starting vacuum...end.
progress: 5.0 s, 1352.2 tps, lat 11.577 ms stddev 17.333
progress: 10.0 s, 1632.6 tps, lat 9.668 ms stddev 5.893
progress: 15.0 s, 1283.6 tps, lat 12.282 ms stddev 10.071
progress: 20.0 s, 334.2 tps, lat 48.063 ms stddev 15.235
progress: 25.0 s, 506.4 tps, lat 31.487 ms stddev 16.478
progress: 30.0 s, 780.2 tps, lat 20.441 ms stddev 5.982
progress: 35.0 s, 773.4 tps, lat 20.634 ms stddev 6.023
progress: 40.0 s, 784.8 tps, lat 20.334 ms stddev 5.763
progress: 45.0 s, 674.0 tps, lat 23.628 ms stddev 7.250
progress: 50.0 s, 552.0 tps, lat 28.319 ms stddev 36.969
progress: 55.0 s, 581.6 tps, lat 28.053 ms stddev 33.092
progress: 60.0 s, 596.0 tps, lat 26.721 ms stddev 29.355
transaction type: <builtin: TPC-B (sort of)>
scaling factor: 100
query mode: simple
number of clients: 16
number of threads: 4
duration: 60 s
number of transactions actually processed: 49272
latency average = 19.379 ms
latency stddev = 19.157 ms
initial connection time = 24.272 ms
tps = 821.035807 (without initial connection time)
```
- Number of transactions: 49272
- Latency average and stddev: 19.379 ms, 19.157
- Initial connection time: 24.272 ms
- Transactions per second: 821.035807

These measurements were performed on the following platform:
```bash
wbjin@node0:~/proj/scripts$ lscpu
Architecture:                aarch64
  CPU op-mode(s):            32-bit, 64-bit
  Byte Order:                Little Endian
CPU(s):                      8
  On-line CPU(s) list:       0-7
Vendor ID:                   APM
  Model:                     1
  Thread(s) per core:        1
  Core(s) per cluster:       8
  Socket(s):                 -
  Cluster(s):                1
  Stepping:                  0x0
  BogoMIPS:                  100.00
  Flags:                     fp asimd evtstrm cpuid
NUMA:
  NUMA node(s):              1
  NUMA node0 CPU(s):         0-7
Vulnerabilities:
  Gather data sampling:      Not affected
  Indirect target selection: Not affected
  Itlb multihit:             Not affected
  L1tf:                      Not affected
  Mds:                       Not affected
  Meltdown:                  Mitigation; PTI
  Mmio stale data:           Not affected
  Reg file data sampling:    Not affected
  Retbleed:                  Not affected
  Spec rstack overflow:      Not affected
  Spec store bypass:         Vulnerable
  Spectre v1:                Mitigation; __user pointer sanitization
  Spectre v2:                Vulnerable
  Srbds:                     Not affected
  Tsa:                       Not affected
  Tsx async abort:           Not affected
  Vmscape:                   Not affected
wbjin@node0:~/proj/scripts$ free -h
               total        used        free      shared  buff/cache   available
Mem:            62Gi       436Mi        58Gi       139Mi       3.7Gi        61Gi
Swap:          8.0Gi          0B       8.0Gi
wbjin@node0:~/proj/scripts$ lsblk -o NAME,TYPE,SIZE,ROTA,MODEL,MOUNTPOINTS
NAME    TYPE   SIZE ROTA MODEL       MOUNTPOINTS
sda     disk 111.8G    0 XR0120GEBLT
├─sda1  part    64G    0             /
├─sda15 part     1M    0
└─sda99 part     8G    0             [SWAP]
```
