"""Plot a raw `perf stat -I <ms> -x ,` CSV over time: IPC, branch miss % (of branches),
hardware events per 1000 instructions, and software events (page faults, context
switches, ...) per second.

    python plot_perf_stat.py perf_stat.csv [out.png]      # default: perf_stat.png next to the csv

Each value is computed per interval. The first interval is dropped. Intervals perf did not
count (<not counted>, e.g. while the program sleeps) are left out, so they show as gaps.
"""
import csv
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# OS events: no relation to instruction count, so plotted per second of the interval.
SOFTWARE = {"page-faults", "minor-faults", "major-faults", "context-switches", "cpu-migrations",
            "alignment-faults", "emulation-faults"}

src = sys.argv[1]
out = sys.argv[2] if len(sys.argv) > 2 else src.rsplit(".", 1)[0] + ".png"

counts = defaultdict(dict)                      # interval end time -> {event: count}
ends = set()                                    # every interval end, counted or not
for row in csv.reader(open(src)):
    if len(row) < 4 or row[0].startswith("#"):
        continue
    ends.add(float(row[0]))
    try:
        counts[float(row[0])][row[3].split(":")[0]] = float(row[1])
    except ValueError:                          # <not counted> / <not supported>
        pass

ends = sorted(ends)
length = {t: t - (ends[i - 1] if i else 0.0) for i, t in enumerate(ends)}   # last one may be shorter

series = defaultdict(lambda: ([], []))          # metric -> (times, values)
for t in sorted(counts)[1:]:                    # drop the first interval
    ev = counts[t]
    ins = ev.get("instructions")
    if not ins:
        continue
    if ev.get("cycles"):
        series["IPC"][0].append(t)
        series["IPC"][1].append(ins / ev["cycles"])
    for e, v in ev.items():
        if e == "branch-misses" and ev.get("branches"):   # own panel, right above branch-misses / 1k instr
            series["branch miss %"][0].append(t)
            series["branch miss %"][1].append(v / ev["branches"] * 100)
        if e in SOFTWARE:
            name, value = f"{e} / s", v / length[t]
        elif e not in ("instructions", "cycles"):
            name, value = f"{e} / 1k instr", v / ins * 1000
        else:
            continue
        series[name][0].append(t)
        series[name][1].append(value)

fig, axes = plt.subplots(len(series), 1, sharex=True, figsize=(10, 2 * len(series)), squeeze=False)
for ax, (name, (ts, vs)) in zip(axes[:, 0], series.items()):
    ax.plot(ts, vs, ".", markersize=3)
    ax.set_ylabel(name, rotation=0, ha="right", va="center")
    ax.grid(alpha=0.3)
axes[-1, 0].set_xlabel("time (s)")
fig.suptitle(src)
fig.tight_layout()
fig.savefig(out, dpi=120)
print(out)
