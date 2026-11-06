"""Plot results from test_strategy.py without modifying the measurements."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


KNOBS = ("shared_buffers", "max_wal_size", "wal_compression")
TITLES = ("Shared buffers", "Maximum WAL size", "WAL compression")
COLORS = ("C0", "C0", "C0")


def load_results(directory):
    runs = defaultdict(list)
    with (directory / "runs.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = float(row["tps"])
            if not 0 < value < float("inf"):
                raise ValueError("TPS values must be finite and positive")
            runs[row["configuration"]].append(value)
    if not runs:
        raise ValueError("runs.csv contains no measurements")
    settings = {}
    for label in runs:
        path = directory / f"{label}-settings.json"
        settings[label] = json.loads(path.read_text())
        for knob in KNOBS:
            if knob not in settings[label]:
                raise ValueError(f"{path} is missing {knob}")
    return runs, settings


def size_key(value):
    """Sort PostgreSQL memory sizes without treating them as lexical strings."""
    import re
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|kB|MB|GB|TB)", value)
    if not match:
        raise ValueError(f"Unsupported memory size: {value}")
    return float(match[1]) * {
        "B": 1, "kB": 1024, "MB": 1024**2,
        "GB": 1024**3, "TB": 1024**4,
    }[match[2]]


def draw_points(ax, groups, labels, color):
    for x, values in enumerate(groups):
        offsets = [
            0 if len(values) == 1 else -0.12 + 0.24 * i / (len(values) - 1)
            for i in range(len(values))
        ]
        ax.plot([x + offset for offset in offsets], values,
                ".", color=color, markersize=3, zorder=3)
        center = median(values)
        ax.errorbar(
            x, center, yerr=[[center - min(values)], [max(values) - center]],
            fmt="_", color=color, markersize=12, capsize=3, zorder=4,
        )
        ax.annotate(f"{center:,.0f}", (x, max(values)),
                    xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=9)
    ax.set_xticks(range(len(labels)), labels)
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.grid(alpha=0.3)
    ax.set_axisbelow(True)


def export(fig, output, stem):
    for extension in ("png", "pdf"):
        path = output / f"{stem}.{extension}"
        fig.savefig(path, dpi=120)
        print(path)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path,
                        help="Output directory; defaults to RESULTS/plots")
    args = parser.parse_args()
    runs, settings = load_results(args.results)
    output = args.output or args.results / "plots"
    output.mkdir(parents=True, exist_ok=True)
    search = [label for label in runs if not label.endswith("-validation")]
    if not search:
        parser.error("No search measurements found")

    metadata = args.results / "arguments.json"
    subtitle = ""
    if metadata.exists():
        options = json.loads(metadata.read_text())
        subtitle = (
            f"Scale {options.get('scale', '?')} | "
            f"Measurement {options.get('duration', '?')} s | "
            f"Warm-up {options.get('warmup', '?')} s"
        )
    else:
        subtitle = "Dataset scale and duration were not recorded in arguments.json"

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharey=True)
    ymax = max(max(runs[label]) for label in search) * 1.20
    for ax, knob, title, color in zip(axes, KNOBS, TITLES, COLORS):
        fixed = [name for name in KNOBS if name != knob]
        contexts = defaultdict(list)
        for label in search:
            key = tuple(settings[label][name] for name in fixed)
            contexts[key].append(label)
        # Select the context with the most distinct values of this parameter.
        # Never pool configurations that differ in the held-fixed settings.
        context, labels = max(
            contexts.items(),
            key=lambda item: len({settings[label][knob] for label in item[1]}),
        )
        groups = defaultdict(list)
        for label in labels:
            groups[settings[label][knob]].extend(runs[label])
        ax.set_title(title)
        ax.set_ylabel("TPS", rotation=0, ha="right", va="center")
        ax.set_ylim(0, ymax)
        ax.set_xlabel("\n".join(
            f"{name} = {value}" for name, value in zip(fixed, context)
        ), fontsize=9)
        if len(groups) < 2:
            ax.text(0.5, 0.5, "No matched comparison available",
                    ha="center", transform=ax.transAxes, fontsize=9)
            ax.set_xticks([])
            continue
        values = sorted(groups, key=(lambda x: x) if knob == "wal_compression"
                        else size_key)
        draw_points(ax, [groups[value] for value in values], values, color)
    fig.suptitle("Hand-tuned PostgreSQL: parameter comparisons\n" + subtitle)
    legend = [
        Line2D([], [], marker=".", markersize=3, linestyle="", color="C0",
               label="Individual repetition"),
        Line2D([], [], marker="_", markersize=12, linestyle="", color="C0",
               label="Median; whiskers show min–max"),
    ]
    fig.legend(handles=legend, loc="lower center", ncol=2,
               frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 0.90))
    export(fig, output, "parameter_comparison")

    validation = ("baseline-validation", "selected-validation")
    if all(label in runs for label in validation):
        fig, ax = plt.subplots(figsize=(10, 5))
        values = [runs[label] for label in validation]
        labels = [
            name + "\n" + "\n".join(
                f"{knob}={settings[label][knob]}" for knob in KNOBS
            )
            for name, label in zip(("Baseline", "Selected"), validation)
        ]
        draw_points(ax, values, labels, "C0")
        ax.set_ylim(0, max(map(max, values)) * 1.20)
        ax.set_ylabel("TPS", rotation=0, ha="right", va="center")
        speedup = median(values[1]) / median(values[0])
        ax.set_title(f"Independent validation: {speedup:.2f}× median TPS")
        fig.legend(handles=legend, loc="lower center", fontsize=8, frameon=False)
        fig.tight_layout(rect=(0, 0.10, 1, 1))
        export(fig, output, "validation_comparison")
    else:
        print("Validation plot skipped: both validation results are required.")


if __name__ == "__main__":
    main()
