"""Plot perf output. The input file decides what is drawn:

    python plot_perf.py perf_stat.csv [out.png]    # default: perf_stat.png next to the csv
    python plot_perf.py perf.data [out.svg]        # default: flamegraph.svg next to perf.data

perf stat CSV (raw `perf stat -I <ms> -x ,`): IPC, branch miss % (of branches), hardware
events per 1000 instructions, and software events (page faults, context switches, ...) per
second, over time. Each value is computed per interval. The first interval is dropped.
Intervals perf did not count (<not counted>, e.g. while the program sleeps) are left out,
so they show as gaps.

perf.data (`perf record -g`): flame graph (SVG), one box per function, width = share of
samples, callers below callees. Hover a box for its name and share. Needs a perf with
libelf to name functions in user-space libraries (see PERF below); otherwise they show as
[library].
"""
import csv
import glob
import os
import subprocess
import sys
import zlib
from collections import defaultdict
from html import escape
from pathlib import Path

# WSL's /usr/bin/perf has no libelf. Ubuntu's perf, unpacked into ~/perf-ubuntu
# (apt download linux-perf; dpkg-deb -x ...), or a linux-tools perf can read symbols.
PERF = os.environ.get("PERF") or next(
    (p for p in [os.path.expanduser("~/perf-ubuntu/usr/bin/perf"),
                 *sorted(glob.glob("/usr/lib/linux-tools/*/perf"))[::-1]] if os.path.exists(p)),
    "perf")

# OS events: no relation to instruction count, so plotted per second of the interval.
SOFTWARE = {"page-faults", "minor-faults", "major-faults", "context-switches", "cpu-migrations",
            "alignment-faults", "emulation-faults"}


def stat(src, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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


def flamegraph(src, out):
    # perf script: "comm tid period" header, then one frame per line (leaf first), blank line between samples
    script = subprocess.run([PERF, "script", "-i", str(src), "-F", "comm,tid,period,ip,sym,dso"],
                            capture_output=True, text=True, check=True).stdout
    stacks = defaultdict(int)
    frames, comm, period = [], None, 0
    for line in script.splitlines() + [""]:
        if not line.strip():
            if comm:
                stacks[";".join([comm] + frames[::-1])] += period
            frames, comm = [], None
        elif line[0] not in " \t":
            head = line.split()
            comm, period = head[0], int(head[-1])
        else:
            _, rest = line.strip().split(" ", 1)               # drop the address
            sym, _, dso = rest.rpartition(" (")
            if sym == "[unknown]":
                lib = Path(dso.rstrip(")")).name
                sym = lib if lib.startswith("[") else f"[{lib}]"
            if not frames or frames[-1] != sym:               # collapse repeats of [library]
                frames.append(sym)

    root = [0, {}]                                            # node = [weight, {child name: node}]
    for stack, w in stacks.items():
        node = root
        node[0] += w
        for f in stack.split(";"):
            node = node[1].setdefault(f, [0, {}])
            node[0] += w

    W, H, PAD = 1200, 16, 10
    boxes = []                                                # (x, depth, width, name, weight)
    def walk(name, node, x, depth):
        boxes.append((x, depth, node[0] / root[0] * (W - 2 * PAD), name, node[0]))
        for child, cn in sorted(node[1].items()):
            walk(child, cn, x, depth + 1)
            x += cn[0] / root[0] * (W - 2 * PAD)
    walk("all", root, PAD, 0)
    height = (max(b[1] for b in boxes) + 1) * H + 3 * PAD + 20

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" font-family="monospace" font-size="11">',
           '<rect width="100%" height="100%" fill="#fafafa"/>',
           f'<text x="{PAD}" y="{PAD + 10}" font-size="13">{escape(str(src))}</text>']
    for x, d, w, name, weight in boxes:
        if w < 0.3:
            continue
        y = height - PAD - (d + 1) * H
        h = zlib.crc32(name.encode())
        color = f"rgb({205 + h % 50},{80 + (h >> 8) % 120},{40 + (h >> 16) % 50})"
        svg.append(f'<g><title>{escape(name)} ({weight / root[0]:.2%})</title>'
                   f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{H - 1}" fill="{color}"/>')
        if w > 30:
            chars = int(w / 7)
            text = name if len(name) <= chars else name[:max(chars - 2, 1)] + ".."
            svg.append(f'<text x="{x + 3:.1f}" y="{y + 11}">{escape(text)}</text>')
        svg.append("</g>")
    svg.append("</svg>")
    Path(out).write_text("\n".join(svg))


if __name__ == "__main__":
    src = Path(sys.argv[1])
    if src.suffix == ".data":
        out = sys.argv[2] if len(sys.argv) > 2 else src.with_name("flamegraph.svg")
        flamegraph(src, out)
    else:
        out = sys.argv[2] if len(sys.argv) > 2 else src.with_suffix(".png")
        stat(str(src), out)
    print(out)
