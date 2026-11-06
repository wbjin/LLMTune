"""Shared parser and per-interval metrics for perf stat -I ... -x ,."""
import csv
import math
from collections import defaultdict

EVENTS = ("cycles,instructions,cache-references,cache-misses,branches,branch-misses,"
          "context-switches,cpu-migrations,page-faults")
RATIOS = {"ipc": ("instructions", "cycles", 1),
          "cache_miss_rate": ("cache-misses", "cache-references", 1),
          "branch_miss_rate": ("branch-misses", "branches", 1)}
SOFTWARE = {"context-switches", "cpu-migrations", "page-faults", "minor-faults",
            "major-faults", "task-clock", "cpu-clock", "alignment-faults", "emulation-faults"}


def parse_intervals(path):
    rows = defaultdict(dict)
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if len(row) < 4:
                continue
            try:
                t = float(row[0])
            except ValueError:
                continue
            if not math.isfinite(t) or t <= 0:
                continue
            events = rows[t]  # Retain timestamps for unsupported/uncounted rows.
            try:
                value = float(row[1])
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0:
                event = row[3].strip()
                # Strip perf modifiers (cycles:u), not tracepoint names
                # (syscalls:sys_enter_pread64 or syscalls:sys_enter_pread64:u).
                base, sep, suffix = event.rpartition(":")
                if sep and suffix and all(c in "ukhGHpPSDIWebR" for c in suffix):
                    event = base
                events[{"cpu-cycles": "cycles", "branch-instructions": "branches"}.get(event, event)] = value
    return sorted(rows.items())


def metrics(ev):
    out = {name: ev[num] / ev[den] * k for name, (num, den, k) in RATIOS.items()
           if ev.get(den) and num in ev}
    if ev.get("instructions"):
        for e, v in ev.items():
            if e not in SOFTWARE and not e.startswith("syscalls:") and e not in ("cycles", "instructions"):
                out[f"{e}_pki"] = v / ev["instructions"] * 1000
    return out


def time_series(intervals):
    """Keep missing events missing; first interval is excluded from derived metrics."""
    rows = []
    for (previous, _), (t, ev) in zip(intervals, intervals[1:]):
        m = metrics(ev)
        out = {}
        for key, label, factor in (("ipc", "IPC", 1), ("branch_miss_rate", "branch miss %", 100),
                                   ("cache_miss_rate", "cache miss %", 100)):
            if key in m:
                out[label] = factor * m[key]
        for key, value in m.items():
            if key.endswith("_pki"):
                out[key[:-4] + " / 1k instr"] = value
        for key in sorted((ev.keys() & SOFTWARE) | {k for k in ev if k.startswith("syscalls:")}):
            unit = " ms/s" if key in {"task-clock", "cpu-clock"} else " / s"
            out[key + unit] = ev[key] / (t - previous)
        rows.append((t, out))
    return rows
