Manual PostgreSQL tuning strategy
=================================

Purpose
-------
Establish a measured human-tuned baseline for the agentic system optimizer.
Optimize sustained pgbench throughput on node0 while preserving durability
and keeping the workload fixed. These are experiments, not proven settings.

Target hardware
---------------
- CPU: 8 aarch64 CPUs, one thread per core, one NUMA node; APM vendor.
- RAM: 62 GiB reported, with approximately 61 GiB initially available.
- Swap: 8 GiB; avoid swapping during benchmark measurements.
- Storage: 111.8 GiB non-rotational XR0120GEBLT disk.
- Root filesystem partition: 64 GiB. This is not the available free space.
- PostgreSQL: 14.24, cluster 14/main, port 5432.

Confirm the data and WAL filesystem and its free space before enlarging WAL
limits or datasets. ROTA=0 does not establish disk latency or write capacity.
The strategy assumes the machine is dedicated to the experiment.

Fixed workload and objective
---------------------------
Use the default pgbench TPC-B-like read/write workload, simple query mode,
scale 100 (10 million account rows), 16 clients, and 4 pgbench worker threads.
The generator shares the machine with PostgreSQL and consumes CPU resources.
Keep these parameters identical for the default, manual, and agent runs.

The initial 60-second run achieved 821 TPS and 19.379 ms average latency.
Throughput fell substantially during that run. Treat it as an exploratory
result, not a stable baseline or evidence of a particular bottleneck.

Measure median TPS across repeated runs, average latency, and variability.
Keep fsync=on, synchronous_commit=on, and full_page_writes=on throughout.

Baseline procedure
------------------
1. Record effective settings from pg_settings and preserve the original
   configuration. Record database size, disk free space, and machine load.
2. Use the same dataset preparation and warm-up for every configuration.
   From the project root, this rebuilds benchmark tables and warms up:
     SCALE=100 CLIENTS=16 THREADS=4 DURATION=120 \
       ./scripts/run_pgbench.sh --clean
   --clean destroys existing benchmark tables in pgbench_test.
3. Measure with CLIENTS=16 THREADS=4 DURATION=600 ./scripts/run_pgbench.sh.
   Repeat the preparation and measurement sequence at least three times.
   Exclude initialization and warm-up from the reported measurement.
4. Keep other machine activity quiet and use the same monitoring throughout.
   Rebuilding tables does not clear OS caches; this is a warm-cache test.
5. Extend measurements if necessary to include multiple checkpoint cycles.
   Re-run the original configuration after candidates to detect run-order
   effects. For tighter control, restore the same database snapshot per trial.

Observe before changing settings
-------------------------------
Collect before/after counter differences from pg_stat_bgwriter, pg_stat_wal,
and pg_stat_database. Sample pg_stat_activity wait events during the run.
Correlate throughput drops with checkpoint activity, disk latency, CPU use,
memory pressure, and lock waits. A PostgreSQL buffer miss may still be served
by the OS cache; do not interpret every miss as physical disk I/O.

Candidate experiments
---------------------
Change one parameter at a time from the best measured configuration:

1. shared_buffers: test 4GB, 8GB, and 16GB if the current value is small.
   Approximately 16GB is a 25%-of-RAM starting heuristic for this host.
   Scale 100 may already fit in cache; more memory need not improve TPS.
   Leave RAM for the OS cache, connections, and pgbench. Requires restart.
2. max_wal_size: test 4GB, then 8GB if disk space permits and requested
   checkpoints are frequent. This is a soft limit, not a disk-usage cap.
   Fewer checkpoints can reduce write pressure but increase recovery work.
   Keep checkpoint_timeout fixed; checkpoint_completion_target=0.9 is
   already the PostgreSQL 14 default. These settings can be reloaded.
3. wal_compression: test on if WAL traffic is significant and CPU headroom
   exists. Compression trades CPU for smaller full-page images in WAL.
   Reload configuration and use new benchmark connections.

Do not raise work_mem merely because RAM is available: this workload is
dominated by small indexed operations, not large sorts. Do not increase
parallel-query workers merely to match eight CPUs; concurrent sessions
already provide parallel execution. Keep autovacuum enabled.

Accepting and reporting a result
-------------------------------
Verify effective settings and pending_restart after applying each candidate.
Keep changes only when improvement exceeds observed run-to-run variability.
Record settings, TPS, latency, counter differences, and keep/revert decisions.
Validate the final combined configuration with longer repeated runs.
Report speedup as tuned median TPS / baseline median TPS, along with trial
count and tuning time. Compare the agent using the same evaluation procedure.
