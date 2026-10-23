# Benchmark Result
```bash
wbjin@node0:~/proj/hand-tuned$ sudo /users/wbjin/.local/bin/uv run test_strategy.py
Testing baseline: {'shared_buffers': '128MB', 'max_wal_size': '1GB', 'wal_compression': 'off'}
  repetition 1: 617.0 TPS
  repetition 2: 583.0 TPS
  repetition 3: 563.0 TPS
Testing candidate-1: {'shared_buffers': '4GB', 'max_wal_size': '1GB', 'wal_compression': 'off'}
  repetition 1: 752.3 TPS
  repetition 2: 719.1 TPS
  repetition 3: 690.3 TPS
Testing candidate-2: {'shared_buffers': '8GB', 'max_wal_size': '1GB', 'wal_compression': 'off'}
  repetition 1: 663.6 TPS
  repetition 2: 634.5 TPS
  repetition 3: 611.1 TPS
Testing candidate-3: {'shared_buffers': '16GB', 'max_wal_size': '1GB', 'wal_compression': 'off'}
  repetition 1: 582.0 TPS
  repetition 2: 571.7 TPS
  repetition 3: 560.3 TPS
Testing candidate-4: {'shared_buffers': '4GB', 'max_wal_size': '4GB', 'wal_compression': 'off'}
  repetition 1: 1347.1 TPS
  repetition 2: 1529.6 TPS
  repetition 3: 1569.0 TPS
Testing candidate-5: {'shared_buffers': '4GB', 'max_wal_size': '8GB', 'wal_compression': 'off'}
  repetition 1: 1515.9 TPS
  repetition 2: 1548.9 TPS
  repetition 3: 1237.2 TPS
Testing candidate-6: {'shared_buffers': '4GB', 'max_wal_size': '4GB', 'wal_compression': 'on'}
  repetition 1: 1445.5 TPS
  repetition 2: 1389.2 TPS
  repetition 3: 1203.9 TPS
Testing baseline-validation: {'shared_buffers': '128MB', 'max_wal_size': '1GB', 'wal_compression': 'off'}
  repetition 1: 416.9 TPS
  repetition 2: 401.2 TPS
  repetition 3: 397.0 TPS
Testing selected-validation: {'shared_buffers': '4GB', 'max_wal_size': '4GB', 'wal_compression': 'off'}
  repetition 1: 1144.0 TPS
  repetition 2: 1232.1 TPS
  repetition 3: 1128.7 TPS
Original configuration restored. Results: /users/wbjin/proj/hand-tuned/handtuned-results-20261022-194249
```

Some notes
- Increasing `shared_buffers` to 4GB improved TPS but increasing to 8GB or 16GB
did not
- Increasing `max_wal_size` to 4GB drastically improved TPS but 4 to 8GB
improvement is not clear
- The "best" configuration from these parameters is `shared_buffers: 4GB,
max_wal_size: 4GB, wal_compression: off`. There seems to be around a 2.85x
improvement from the baseline

- **128 MB → 4 GB:** Provides enough room to cache the database, consistent
with the improvement you measured.
- **4 GB → 8 GB or 16 GB:** Adds capacity that this dataset does not need. It
cannot eliminate many additional reads once the relevant pages are cached.

The benchmark database occupied approximately 1.6 GB, making a 4 GB PostgreSQL
buffer pool sufficient to accommodate it. Increasing buffer capacity further
offered limited opportunity to improve cache residency, so performance became
constrained by other factors, such as WAL and checkpoint I/O.
