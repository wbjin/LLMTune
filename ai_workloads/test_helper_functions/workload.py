"""The workload: one `llama bench` invocation -> one Measurement.

A config is a dict {knob_name: value} holding ONLY the knobs being changed.
Anything absent is left at the program's own default, so `{}` is the baseline.
System knobs (sysfs, powercfg) are not applied here; see system.py.
"""
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field

from . import platforms as P


@dataclass
class Workload:
    """What is measured. Fixed for a whole experiment, never tuned."""
    model: str = ""         # "" = the platform's default model (params/<platform>.toml)
    n_prompt: int = 512     # prompt-processing test  -> TTFT proxy
    n_gen: int = 128        # token-generation test   -> TPS
    n_depth: int = 0        # pre-filled context before each test
    reps: int = 5           # llama bench repetitions per test
    delay: int = 10         # seconds between llama bench tests (thermal)
    timeout: int = 1800


@dataclass
class Measurement:
    ok: bool
    pp_tps: float = 0.0          # prompt tokens/s
    pp_tps_sd: float = 0.0
    tg_tps: float = 0.0          # generated tokens/s  (throughput)
    tg_tps_sd: float = 0.0
    ttft_s: float = 0.0          # n_prompt / pp_tps   (latency proxy, excludes sampling)
    tpot_ms: float = 0.0         # 1000 / tg_tps       (time per output token)
    wall_s: float = 0.0
    error: str = ""
    raw: list = field(default_factory=list, repr=False)

    def to_dict(self):
        return asdict(self)


def _fmt(knob, value):
    return ("1" if value else "0") if knob.type == "bool" else str(value)


def build(cfg, knobs, wl):
    """Return (argv, env) for a config. Pure; nothing is executed."""
    plat = P.get()
    argv = [plat.llama, "bench", "-m", plat.host_path(wl.model or plat.model), "-o", "json",
            "-p", str(wl.n_prompt), "-n", str(wl.n_gen), "-d", str(wl.n_depth),
            "-r", str(wl.reps), "--delay", str(wl.delay)]
    env = dict(os.environ)
    for name, value in cfg.items():
        k = knobs[name]
        if k.layer == "flag":
            argv += [k.key, _fmt(k, value)]
        elif k.layer == "env":
            if k.type == "bool":
                env.pop(k.key, None)
                if value:
                    env[k.key] = "1"
            else:
                env[k.key] = str(value)
    if plat.windows_exe:        # WSL interop passes only the env vars listed in WSLENV
        ggml = [k for k in env if k.startswith("GGML_")]
        env["WSLENV"] = ":".join(filter(None, [os.environ.get("WSLENV", "")] + ggml))
    return argv, env


def parse(stdout, wl):
    tests = json.loads(stdout)
    pp = next((t for t in tests if t["n_prompt"] > 0 and t["n_gen"] == 0), None)
    tg = next((t for t in tests if t["n_gen"] > 0 and t["n_prompt"] == 0), None)
    m = Measurement(ok=True, raw=tests)
    if pp:
        m.pp_tps, m.pp_tps_sd = pp["avg_ts"], pp["stddev_ts"]
        m.ttft_s = wl.n_prompt / m.pp_tps
    if tg:
        m.tg_tps, m.tg_tps_sd = tg["avg_ts"], tg["stddev_ts"]
        m.tpot_ms = 1000.0 / m.tg_tps
    return m


def run(cfg, knobs, wl):
    import time
    argv, env = build(cfg, knobs, wl)
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=wl.timeout)
    except subprocess.TimeoutExpired:
        return Measurement(ok=False, error="timeout", wall_s=wl.timeout)
    wall = time.monotonic() - t0
    if p.returncode != 0:
        return Measurement(ok=False, error=p.stderr[-2000:], wall_s=wall)
    try:
        m = parse(p.stdout, wl)
    except (json.JSONDecodeError, KeyError, ZeroDivisionError) as e:
        return Measurement(ok=False, error=f"parse: {e}\n{p.stdout[-1000:]}", wall_s=wall)
    m.wall_s = wall
    return m
