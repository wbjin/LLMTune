"""OpenTuner adapter for a fixed PostgreSQL pgbench workload."""

import random
import signal
import time
from pathlib import Path

import numpy as np
import opentuner
from opentuner import ConfigurationManipulator, EnumParameter
from opentuner import MeasurementInterface, Result
from opentuner.tuningrunmain import TuningRunMain

from postgres_benchmark import PostgresBenchmark, SPACE, positive


class PostgresTuner(MeasurementInterface):
    def __init__(self, args, benchmark):
        super().__init__(args=args, project_name="postgres",
                         program_name="pgbench", program_version="14")
        self.benchmark = benchmark
        self.evaluations = 0

    def manipulator(self):
        manipulator = ConfigurationManipulator()
        for name, choices in SPACE.items():
            manipulator.add_parameter(EnumParameter(name, choices))
        return manipulator

    def run(self, desired_result, input, limit):
        # OpenTuner derives limit from prior Result.time scores. Our score
        # is inverse TPS, not elapsed seconds; command timeouts are separate.
        self.evaluations += 1
        result = self.benchmark.evaluate(
            desired_result.configuration.data, f"trial-{self.evaluations:04d}"
        )
        return Result(time=1.0 / result["median_tps"])

    def save_final_config(self, configuration):
        self.benchmark.save(
            self.benchmark.output / "best-config.json", configuration.data
        )


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"Received signal {signum}")


def main():
    parser = opentuner.default_argparser()
    parser.add_argument("--duration", type=positive, default=600)
    parser.add_argument("--warmup", type=positive, default=120)
    parser.add_argument("--repeats", type=positive, default=3)
    parser.add_argument("--scale", type=positive, default=100)
    parser.add_argument("--search-seed", type=int, default=1)
    parser.add_argument("--output", default=None)
    parser.set_defaults(parallelism=1, test_limit=12)
    args = parser.parse_args()
    if args.parallelism != 1 or args.parallel_compile or args.pipelining:
        parser.error("Only serial, non-pipelined evaluation is supported")
    if args.test_limit is None or args.test_limit <= 0:
        parser.error("--test-limit must be positive")
    random.seed(args.search_seed)
    np.random.seed(args.search_seed)
    signal.signal(signal.SIGTERM, interrupted)
    output = Path(args.output or time.strftime("opentuner-results-%Y%m%d-%H%M%S"))
    benchmark = PostgresBenchmark(
        output, args.duration, args.warmup, args.repeats, args.scale
    )
    # Use a fresh search database for every invocation.
    args.database = "sqlite:///" + str(benchmark.output / "search.db")
    with benchmark.session():
        benchmark.save(benchmark.output / "arguments.json", vars(args))
        TuningRunMain(PostgresTuner(args, benchmark), args).main()
    print(f"Results: {benchmark.output}")


if __name__ == "__main__":
    main()
