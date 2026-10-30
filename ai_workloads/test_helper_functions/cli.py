"""Command-line helpers shared by the run_*.py scripts."""
import json
from pathlib import Path

from . import knobs as K
from . import platforms as P
from . import workload as W


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str))


def workload_args(p):
    d = W.Workload()
    p.add_argument("--platform", choices=P.PLATFORMS, default=P.detect(),
                   help="params/<platform>.toml to load with params/llama.toml (default: %(default)s)")
    p.add_argument("--model", default=None, help="default: the platform's model")
    p.add_argument("-p", "--n-prompt", type=int, default=d.n_prompt)
    p.add_argument("-n", "--n-gen", type=int, default=d.n_gen)
    p.add_argument("-d", "--n-depth", type=int, default=d.n_depth)
    p.add_argument("--reps", type=int, default=d.reps, help="llama bench -r")
    p.add_argument("--delay", type=int, default=d.delay, help="llama bench --delay: sleep before each test (s)")
    p.add_argument("--timeout", type=int, default=d.timeout)


def workload_from(a):
    plat = P.select(a.platform)
    return W.Workload(model=a.model or plat.model, n_prompt=a.n_prompt, n_gen=a.n_gen, n_depth=a.n_depth,
                      reps=a.reps, delay=a.delay, timeout=a.timeout)


def load_config(p, a, reg):
    """Read and validate --config. Returns {} for the baseline."""
    cfg = json.loads(Path(a.config).read_text()) if a.config else {}
    if bad := set(cfg) - reg.keys():
        p.error(f"unknown knobs: {sorted(bad)}")
    if errs := K.violations(cfg):
        p.error("; ".join(errs))
    if any(reg[n].layer in K.SYSTEM_LAYERS for n in cfg):
        p.error("system knobs (sysfs/powercfg) are set by the tuner (--allow-system), not here")
    return cfg


def config_name(a):
    """Config part of the run folder name: --label, "default", "best-<objective>" for a
    best_config.json from an OpenTuner run folder, else the config file stem."""
    if a.label:
        return a.label
    if not a.config:
        return "default"
    meta = Path(a.config).parent / "meta.json"
    if meta.exists() and (obj := json.loads(meta.read_text()).get("objective")):
        return f"best-{obj}"
    return Path(a.config).stem


def shell_command(argv, env):
    """One copy-pasteable line: GGML_* overrides followed by the command."""
    import shlex
    env_over = {k: env[k] for k in env if k.startswith("GGML_")}
    prefix = " ".join(f"{k}={v}" for k, v in env_over.items())
    return (prefix + " " if prefix else "") + shlex.join(argv), env_over
