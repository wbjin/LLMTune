# LLMTune

The idea of this project is to use LLMs as offline tuners of systems. We will
give the LLM benchmark results and profiling data so it can suggest configuration
changes and use the results to guide further experiments. We will also explore
whether access to the target system's source code helps it understand how
parameters affect performance.

## Motivation

Tuning a system often requires testing many configurations, and each benchmark
run can be expensive. We want to investigate whether an LLM can use its systems
knowledge and feedback from previous experiments to find better configurations
with fewer benchmark runs.

Our focus is offline tuning. This allows us to explore configurations more
aggressively without affecting a live application, test settings that require
restarts, and spend more time analyzing results before choosing the next
configuration. We will measure whether these choices improve performance within
a fixed tuning budget, including the time spent running benchmarks and using
the LLM.

## Approach

We plan to start with the default configuration and measure its performance
under a fixed workload. The LLM will receive the tuning objective, hardware and
workload information, available parameters, and previous benchmark results. It
will then suggest a configuration to test. After each experiment, we will return
the measured performance and any failures to the LLM.

We will explore two ways to use the LLM: directly suggesting configurations, and
guiding OpenTuner by selecting parameters, narrowing their ranges, or suggesting
starting configurations. We will also compare the effect of adding profiling
data and potentially source-code access. The final configuration will be tested
in separate, repeated benchmark runs.

## Current progress

We currently have benchmarking, baseline tuning, and profiling scripts for
PostgreSQL and llama.cpp. The LLM tuning loop and source-code access are planned.

- **PostgreSQL:** pgbench measurements, manual tuning experiments, an OpenTuner
  adapter, and server-side perf profiling. The experiments include buffer, WAL,
  and commit-related settings. See the [PostgreSQL README](postgres/README.md),
  [OpenTuner README](postgres/opentuner/README.md), and
  [profiling instructions](postgres/PERF.md).
- **llama.cpp:** llama bench measurements, an OpenTuner baseline, and perf
  counter and call-graph collection. See the
  [AI workloads README](ai_workloads/README.md).

## Evaluation

We will compare the LLM approaches against default settings, manual tuning where
available, and plain OpenTuner. Each comparison will use the same workload,
search space, and tuning budget. We will measure:

- Throughput and latency of the selected configuration.
- Number of benchmark runs needed to reach a target performance.
- Total tuning time and LLM API cost.
- Failed experiments and variation across repeated runs.

We will validate the selected configurations separately from the tuning runs
and test them under workload settings that were not used during tuning. The
current results are baseline measurements; we have not yet evaluated the LLM
approaches.

## Related work

[Liargkovas et al.](https://doi.org/10.1145/3830418.3843885) present TuxBot, a
system that uses LLMs to tune operating-system parameters while an application
is running. The LLM uses parameter descriptions, system metrics, application
performance when available, and previous tuning results to suggest configuration
changes. Because TuxBot operates online, it must limit disruptions to the running
application and make timely tuning decisions, which constrains both exploration
and reasoning time. Our project instead focuses on offline tuning, allowing more
aggressive exploration and more time to analyze benchmark results before
choosing the next configuration. We will investigate whether this flexibility,
potentially combined with access to the target system's source code, helps the
LLM find better configurations within a fixed tuning budget.
