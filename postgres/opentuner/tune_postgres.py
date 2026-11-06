"""OpenTuner adapter for a fixed PostgreSQL pgbench workload."""

import json
import random
from statistics import median
import signal
import time
from pathlib import Path

import numpy as np
import opentuner
from opentuner import ConfigurationManipulator, EnumParameter
from opentuner import MeasurementInterface, Result
from opentuner.tuningrunmain import TuningRunMain

from postgres_benchmark import PostgresBenchmark, SPACE, SEARCH_SPACES, positive


class PostgresTuner(MeasurementInterface):
    def __init__(self, args, benchmark):
        super().__init__(args=args, project_name="postgres",
                         program_name="pgbench", program_version="14")
        self.benchmark = benchmark
        self.evaluations = 0
        self.requests = 0
        self.cache = {}

    def manipulator(self):
        manipulator = ConfigurationManipulator()
        for name, choices in self.benchmark.space.items():
            manipulator.add_parameter(EnumParameter(name, choices))
        return manipulator

    def run(self, desired_result, input, limit):
        # OpenTuner derives limit from prior Result.time scores. Our score
        # is inverse TPS, not elapsed seconds; command timeouts are separate.
        config = desired_result.configuration.data
        key = tuple(config[name] for name in self.benchmark.space)
        self.requests += 1
        if key not in self.cache:
            self.evaluations += 1
            self.cache[key] = self.benchmark.evaluate(
                config, f"trial-{self.evaluations:04d}"
            )
        else:
            print("Reusing this configuration's search measurement.", flush=True)
        self.benchmark.save(self.benchmark.output / "search-counts.json", {
            "adapter_requests": self.requests,
            "unique_evaluations": self.evaluations,
            "cached_requests": self.requests - self.evaluations,
        })
        return Result(time=1.0 / self.cache[key]["median_tps"])

    def save_final_config(self, configuration):
        self.benchmark.save(
            self.benchmark.output / "best-config.json", configuration.data
        )


def load_manual_config(path, space=SPACE):
    path = Path(path)
    config = json.loads(path.read_text())["selected"]
    if set(config) - set(space):
        raise ValueError("Manual selection contains unknown search knobs")
    missing = set(space) - set(config)
    if missing:
        # Recover actual historical values, rather than inventing new seed settings.
        settings = json.loads((path.parent / "selected-validation-settings.json").read_text())
        config.update({name: settings[name] for name in missing})
    if set(config) != set(space):
        raise ValueError("Manual selection must contain exactly the search knobs")
    for name, choices in space.items():
        if config[name] not in choices:
            raise ValueError(f"Manual {name}={config[name]} is outside the search space")
    return config


def validate_comparison(benchmark, manual, selected, duration, rounds):
    """Fresh AB/BA measurements; neither search scores nor cache are reused."""
    old_duration, old_repeats = benchmark.duration, benchmark.repeats
    samples = {"manual": [], "opentuner": []}
    configs = {"manual": manual, "opentuner": selected}
    try:
        benchmark.duration, benchmark.repeats = duration, 1
        for index in range(rounds):
            order = ("manual", "opentuner") if index % 2 == 0 else ("opentuner", "manual")
            for name in order:
                result = benchmark.evaluate(
                    configs[name], f"validation-{index + 1:02d}-{name}"
                )
                samples[name].append(result["median_tps"])
    finally:
        benchmark.duration, benchmark.repeats = old_duration, old_repeats
    medians = {name: median(values) for name, values in samples.items()}
    summary = {
        "configurations": configs, "duration": duration,
        "warmup": benchmark.warmup, "scale": benchmark.scale,
        "samples_tps": samples, "median_tps": medians,
        "opentuner_over_manual": medians["opentuner"] / medians["manual"],
        "same_configuration": manual == selected,
        "note": "Independent alternating validation; ratio alone is not a significance test.",
    }
    benchmark.save(benchmark.output / "validation-summary.json", summary)
    print(f"Validation: {summary['opentuner_over_manual']:.3f}x manual TPS", flush=True)
    return summary


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"Received signal {signum}")


def main():
    parser = opentuner.default_argparser()
    parser.add_argument(
        "--fast", action="store_true",
        help="Match hand-tuned fast mode: 30s test, 5s warm-up, "
             "1 repetition, scale 10; rebuild data before each repetition.",
    )
    parser.add_argument("--duration", type=positive, default=None)
    parser.add_argument("--warmup", type=positive, default=None)
    parser.add_argument("--repeats", type=positive, default=None)
    parser.add_argument("--scale", type=positive, default=None)
    parser.add_argument("--search-space", choices=SEARCH_SPACES, default="original")
    parser.add_argument("--search-seed", type=int, default=1)
    parser.add_argument("--output", default=None)
    parser.add_argument("--manual-summary", type=Path,
                        help="Seed search from a hand-tuned summary.json")
    parser.add_argument("--validate", action="store_true",
                        help="Compare selection against manual seed using fresh alternating runs")
    parser.add_argument("--validation-duration", type=positive, default=60)
    parser.add_argument("--validation-rounds", type=positive, default=3)
    parser.set_defaults(parallelism=1, test_limit=12)
    args = parser.parse_args()
    defaults = (30, 5, 1, 10) if args.fast else (600, 120, 3, 100)
    for name, default in zip(("duration", "warmup", "repeats", "scale"), defaults):
        if getattr(args, name) is None:
            setattr(args, name, default)
    if args.parallelism != 1 or args.parallel_compile or args.pipelining:
        parser.error("Only serial, non-pipelined evaluation is supported")
    if args.test_limit is None or args.test_limit <= 0:
        parser.error("--test-limit must be positive")
    if args.validate and not args.manual_summary:
        parser.error("--validate requires --manual-summary")
    try:
        manual = load_manual_config(args.manual_summary, SEARCH_SPACES[args.search_space]) if args.manual_summary else None
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    if args.manual_summary:
        args.manual_summary = str(args.manual_summary.resolve())
    random.seed(args.search_seed)
    np.random.seed(args.search_seed)
    signal.signal(signal.SIGTERM, interrupted)
    output = Path(args.output or time.strftime("opentuner-results-%Y%m%d-%H%M%S"))
    benchmark = PostgresBenchmark(
        output, args.duration, args.warmup, args.repeats, args.scale,
        reuse_dataset=False, search_space=args.search_space,
    )
    # Use a fresh search database for every invocation.
    args.database = "sqlite:///" + str(benchmark.output / "search.db")
    with benchmark.session():
        benchmark.save(benchmark.output / "search-space.json", benchmark.space)
        if manual is not None:
            seed_path = benchmark.output / "manual-seed.json"
            benchmark.save(seed_path, manual)
            args.seed_configuration = [str(seed_path), *args.seed_configuration]
        benchmark.save(benchmark.output / "arguments.json", vars(args))
        search_started = time.monotonic()
        tuner = PostgresTuner(args, benchmark)
        TuningRunMain(tuner, args).main()
        benchmark.save(benchmark.output / "search-summary.json", {
            "elapsed_seconds": time.monotonic() - search_started,
            "unique_evaluations": tuner.evaluations,
            "manual_seeded": manual is not None,
            "request_budget": args.test_limit,
        })
        if args.validate:
            selected = json.loads((benchmark.output / "best-config.json").read_text())
            validate_comparison(benchmark, manual, selected,
                                args.validation_duration, args.validation_rounds)
    print(f"Results: {benchmark.output}")


if __name__ == "__main__":
    main()
