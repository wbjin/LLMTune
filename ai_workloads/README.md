# ai_workloads: baseline measurements for llama.cpp autotuning

The project asks whether an LLM can make OpenTuner cheaper: fewer evaluations and less
wall time to reach a good config, by giving it a narrowed search space and seed configs.
This folder produces the **baselines** that comparison needs:

1. **Default performance**: llama.cpp with nothing changed (`run_baseline.py`).
2. **Plain OpenTuner**: the tuning trajectory over the full knob space, with score and
   time per evaluation (`run_opentune_base.py`).
3. **Program properties**: perf profiles of the default config and the best config (`run_perf.py`).

The LLM step (`run_with_llm.py`) is not implemented yet. Its output will be a space file
(`{"enable": [...], "ranges": {...}, "seeds": [...]}`) passed to `run_opentune_base.py --space`.
OpenTuner itself is not modified.

How to run the preliminary experiments: [RUNNING.md](RUNNING.md).

## Layout

```
ai_workloads/
├── run_baseline.py          default config (or --config) N times
├── run_opentune_base.py     plain OpenTuner over the knob space (--space: guided)
├── run_perf.py              one run under perf: `stat` (counters) or `record` (call graphs)
├── run_with_llm.py          LLM step: only --context-only works
├── params/                  the knob space, one file per platform (see below)
├── test_helper_functions/   helpers for the run_*.py scripts only
└── runs/                    output (git-ignored)
```

Every script takes `--platform` and the workload flags `--model -p -n -d --reps --delay --timeout`;
`run_baseline.py` and `run_perf.py` also `--dry-run` (print the command, run nothing). Output goes to
`runs/<timestamp>_llama-<model>_<config>_<test>/` (`<config>`: `default`, `best-<objective>`, or `--label`;
OpenTuner: `..._opentune-base_<objective>`), always with `REPORT.md` and `meta.json`.

## Workload

`llama bench` (llama.cpp b11429) with Qwen3.5-0.8B Q8_0: a prompt-processing test
(`-p`, default 512 tokens) and a token-generation test (`-n`, default 128 tokens),
`--reps` repetitions each (default 5). Metrics:

| metric | meaning |
|---|---|
| `tg_tps` | generation speed, tok/s (throughput) |
| `pp_tps` | prompt processing speed, tok/s |
| `ttft_s` | `n_prompt / pp_tps` (time-to-first-token proxy) |
| `tpot_ms` | `1000 / tg_tps` |

Tuning objectives (`--objective`), normalised so the baseline scores 1.0:
`throughput` = `tg_tps / tg_tps0`, `latency` = `ttft0 / ttft`,
`weighted` = `w·throughput + (1−w)·latency`.

## Knob space: `params/`

A run loads `params/llama.toml` plus `params/<platform>.toml`.

| file | contents |
|---|---|
| `llama.toml` | llama bench flags and `GGML_*` env vars, every platform |
| `wsl.toml` | desktop WSL2, CPU only; THP sysfs knobs; borrows the power-plan knobs from `windows.toml` |
| `windows.toml` | desktop Windows `llama.exe` (Vulkan) launched from WSL; Windows power plan via `powercfg` |
| `linux-native.toml` | laptop native Linux: cpufreq/EPP/turbo/SMT/THP/iGPU sysfs |

A platform file has `[platform]` (binary, model, `gpu`), `[override.<knob>]` (host-specific
ranges) and its own system knobs. List a space: `python -m test_helper_functions.knobs --platform wsl [--enabled]`.

Knob layers: `flag` (llama bench argument), `env` (environment variable), `sysfs` (root write,
`sudo -n tee`), `powercfg` (Windows power plan, no admin). System layers (`sysfs`, `powercfg`)
are only tuned with `--allow-system`, and are restored on exit. A config is a dict of the knobs
being changed; `{}` is the default.

## Outputs worth knowing

- `run_baseline.py`: `summary.json` (mean/sd/min/max per metric), `trial_NN.json`.
- `run_opentune_base.py`: `trials.jsonl`, one line per evaluation (config, measurement, score,
  `elapsed_s`, `finished_at`): the data for a progress plot. `best_config.json` at the end.
- `run_perf.py stat`: metrics (IPC, miss rates, events per 1000 instructions) computed per
  interval, first interval discarded, then summarised. Raw counts are in `perf_stat.csv`
  and `intervals.csv`, for debugging only.
- `run_perf.py record`: `perf.data`, `top_symbols.txt`.

## Known limits

- OpenTuner needs a patch with SQLAlchemy 2.x (see the top-level CLAUDE.md), or duplicate configs are re-run.
- `--test-limit` also counts duplicates and constraint-rejected configs. Count real evaluations in `trials.jsonl`.
- Some random configs are extremely slow: one smoke-test evaluation took 549 s. `--timeout`
  (default 1800 s) caps a single evaluation.
- WSL: no GPU, no CPU frequency control; perf has only generic events (no energy, frequency
  or memory bandwidth). `--platform windows` cannot be profiled with Linux perf.
- The system snapshot in each report describes the machine the harness runs on (the WSL VM
  for `--platform windows`).
