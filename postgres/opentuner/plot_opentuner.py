"""Plot completed OpenTuner trials using the hand-tuned plotting style."""

import argparse
from collections import defaultdict
import importlib.util
import json
import math
from pathlib import Path
from statistics import median


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    # Share the established style without importing either benchmark runner.
    style_path = (
        Path(__file__).resolve().parents[1]
        / "hand-tuned" / "plot_handtuned.py"
    )
    spec = importlib.util.spec_from_file_location("handtuned_plot", style_path)
    style = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(style)
    plt = style.plt

    trials = []
    for directory in sorted(args.results.glob("trial-*")):
        result_path = directory / "result.json"
        if not result_path.exists():
            print(f"Skipping incomplete trial: {directory.name}")
            continue
        config = json.loads((directory / "config.json").read_text())
        result = json.loads(result_path.read_text())
        values = [float(row["tps"]) for row in result["measurements"]]
        if not values or any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError(f"Invalid TPS measurements in {result_path}")
        for knob in style.KNOBS:
            if knob not in config:
                raise ValueError(f"Missing {knob} in {directory}")
        trials.append((directory.name, config, values))
    if not trials:
        parser.error("No completed trials found")

    knobs = tuple(trials[0][1])
    if any(set(config) != set(knobs) for _, config, _ in trials):
        parser.error("Trials have inconsistent parameter sets")
    titles = dict(zip(style.KNOBS, style.TITLES))
    titles.update(commit_delay="Commit delay (microseconds)",
                  commit_siblings="Commit siblings (concurrent transactions)")

    options = json.loads((args.results / "arguments.json").read_text())
    subtitle = (
        f"Scale {options['scale']} | Measurement {options['duration']} s | "
        f"Warm-up {options['warmup']} s"
    )
    output = args.output or args.results / "plots"
    output.mkdir(parents=True, exist_ok=True)
    ymax = max(max(values) for _, _, values in trials) * 1.20
    legend = [
        style.Line2D([], [], marker=".", markersize=3, linestyle="",
                     color="C0", label="Individual repetition"),
        style.Line2D([], [], marker="_", markersize=12, linestyle="",
                     color="C0", label="Median; whiskers show min–max"),
    ]

    fig, ax = plt.subplots(figsize=(max(10, len(trials) * 1.5), 5 + 0.3 * max(0, len(knobs) - 3)))
    best = max(trials, key=lambda trial: median(trial[2]))[0]
    labels = [
        name + (" *" if name == best else "") + "\n"
        + "\n".join(f"{knob}={config[knob]}" for knob in knobs)
        for name, config, _ in trials
    ]
    style.draw_points(ax, [values for _, _, values in trials], labels, "C0")
    ax.tick_params(axis="x", labelsize=8)
    ax.set_ylim(0, ymax)
    ax.set_ylabel("TPS", rotation=0, ha="right", va="center")
    ax.set_title("OpenTuner configurations (* highest observed median)\n" + subtitle)
    fig.legend(handles=legend, loc="lower center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    style.export(fig, output, "configuration_comparison")

    fig, axes = plt.subplots(len(knobs), 1, figsize=(10, 3 * len(knobs)),
                             sharey=True, squeeze=False)
    for ax, knob in zip(axes[:, 0], knobs):
        title = titles.get(knob, knob)
        fixed = [name for name in knobs if name != knob]
        contexts = defaultdict(list)
        for trial in trials:
            contexts[tuple(trial[1][name] for name in fixed)].append(trial)
        context, matching = max(
            contexts.items(),
            key=lambda item: len({trial[1][knob] for trial in item[1]}),
        )
        groups = defaultdict(list)
        for _, config, values in matching:
            groups[config[knob]].extend(values)
        ax.set_title(title)
        ax.set_ylabel("TPS", rotation=0, ha="right", va="center")
        ax.set_ylim(0, ymax)
        ax.set_xlabel(", ".join(
            f"{name} = {value}" for name, value in zip(fixed, context)
        ), fontsize=9)
        if len(groups) < 2:
            ax.text(0.5, 0.5, "No matched comparison available",
                    ha="center", transform=ax.transAxes)
            ax.set_xticks([])
            ax.grid(alpha=0.3)
            continue
        sort_key = (style.size_key if knob in ("shared_buffers", "max_wal_size")
                    else float if knob in ("commit_delay", "commit_siblings")
                    else str)
        values = sorted(groups, key=sort_key)
        style.draw_points(ax, [groups[v] for v in values], values, "C0")
    fig.suptitle("OpenTuner: matched parameter comparisons\n" + subtitle)
    fig.legend(handles=legend, loc="lower center", ncol=2, frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 0.90))
    style.export(fig, output, "parameter_comparison")


if __name__ == "__main__":
    main()
