"""Plot one interval CSV or overlay two. The original positional CLI is supported."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from perf_intervals import parse_intervals, time_series


def plot(paths, labels, output):
    data = [time_series(parse_intervals(p)) for p in paths]
    names = list(dict.fromkeys(k for rows in data for _, values in rows for k in values))
    if not names or any(not any(values for _, values in rows) for rows in data):
        raise ValueError("No plottable counters after the first interval; inspect raw CSV, events and permissions")
    fig, axes = plt.subplots(len(names), 1, sharex=True, figsize=(10, 2 * len(names)), squeeze=False)
    for ax, name in zip(axes[:, 0], names):
        for rows, label in zip(data, labels):
            ax.plot([t for t, _ in rows], [v.get(name, float("nan")) for _, v in rows],
                    ".", markersize=3, label=label)
        ax.set_ylabel(name, rotation=0, ha="right", va="center")
        ax.grid(alpha=0.3)
    if len(paths) > 1:
        axes[0, 0].legend()
    axes[-1, 0].set_xlabel("time (s)")
    fig.suptitle(str(paths[0]) if len(paths) == 1 else "PostgreSQL server counters: " + " vs ".join(labels))
    fig.tight_layout()
    fig.savefig(output, dpi=120)
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path)
    p.add_argument("output", nargs="?", type=Path)
    p.add_argument("--compare", type=Path)
    p.add_argument("--labels", nargs=2, default=("default", "optimized"))
    a = p.parse_args()
    plot([a.csv, a.compare] if a.compare else [a.csv], a.labels, a.output or a.csv.with_suffix(".png"))
