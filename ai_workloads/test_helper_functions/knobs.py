"""Knob registry: load params/llama.toml + params/<platform>.toml, narrow it with a space
file, check constraints.

    python -m test_helper_functions.knobs                      # full parameter table (markdown), detected platform
    python -m test_helper_functions.knobs --platform windows   # another platform
    python -m test_helper_functions.knobs --enabled            # only the knobs in the default search space
"""
import argparse
import json
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import platforms as P

LAYERS = ("flag", "env", "sysfs", "powercfg")
SYSTEM_LAYERS = ("sysfs", "powercfg")     # host state: applied by the tuner only with --allow-system


@dataclass(frozen=True)
class Knob:
    name: str
    layer: str
    key: str
    type: str
    default: object = None
    min: int | None = None
    max: int | None = None
    values: list | None = None
    root: bool = False
    quality: bool = False
    enabled: bool = True
    gpu: bool = False
    note: str = ""
    file: str = ""          # params file the knob came from

    def domain(self):
        if self.type in ("int", "pow2"):
            return f"{self.min}..{self.max}" + (" (pow2)" if self.type == "pow2" else "")
        if self.type == "bool":
            return "0/1"
        return ", ".join(self.values)


def _knobs_of(name, data):
    return [Knob(**k, file=f"{name}.toml") for k in data.get("knob", [])]


def load(platform=None):
    """Registry for a platform: llama.toml, then the platform file (plus `knobs_from`),
    then the platform's [override.<knob>] tables; gpu knobs disabled when gpu = false."""
    plat = P.select(platform)
    pdata = P.read(plat.name)
    llama = tomllib.loads((P.PARAMS / "llama.toml").read_text())
    knobs = {}
    sources = [("llama", llama), (plat.name, pdata)]
    sources += [(n, P.read(n)) for n in pdata.get("platform", {}).get("knobs_from", [])]
    for src, data in sources:
        for k in _knobs_of(src, data):
            assert k.name not in knobs, f"{k.name} defined twice ({knobs.get(k.name, k).file}, {k.file})"
            knobs[k.name] = k
    for name, fields in pdata.get("override", {}).items():
        assert name in knobs, f"{plat.name}.toml overrides unknown knob {name}"
        knobs[name] = replace(knobs[name], **fields)
    if not plat.gpu:
        knobs = {n: replace(k, enabled=False) if k.gpu else k for n, k in knobs.items()}
    for k in knobs.values():
        assert k.layer in LAYERS, f"{k.name}: bad layer {k.layer}"
        assert k.type in ("int", "pow2", "enum", "bool"), f"{k.name}: bad type {k.type}"
    return knobs


def apply_space(knobs, space):
    """Narrow/re-enable knobs from a space dict (the LLM advisor's output format):

    {"enable": ["threads", ...],            # replaces the enabled set
     "ranges": {"threads": {"min": 2, "max": 8},
                "device": {"values": ["Vulkan0"]}},
     "seeds":  [{"threads": 4, "batch": 512}]}  # consumed by run_opentune_base.py
    """
    enable = set(space.get("enable", [k for k, v in knobs.items() if v.enabled]))
    unknown = enable - knobs.keys() | space.get("ranges", {}).keys() - knobs.keys()
    if unknown:
        raise KeyError(f"space file references unknown knobs: {sorted(unknown)}")
    out = {}
    for name, k in knobs.items():
        k = replace(k, enabled=name in enable, **space.get("ranges", {}).get(name, {}))
        out[name] = k
    return out


def load_space(path):
    return json.loads(Path(path).read_text()) if path else {}


def _smt_sibling_mask():
    """Bitmask of the logical CPUs that go offline with SMT off (all but the first
    thread of each core). 0 if the topology is not readable (non-Linux)."""
    mask = 0
    for f in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/topology/thread_siblings_list"):
        ids = []
        for part in f.read_text().strip().split(","):
            lo, _, hi = part.partition("-")
            ids += range(int(lo), int(hi or lo) + 1)
        for i in sorted(ids)[1:]:
            mask |= 1 << i
    return mask


def violations(cfg):
    """Cross-knob constraints. cfg holds only the knobs being set; missing = default."""
    errs = []
    if cfg.get("ubatch", 512) > cfg.get("batch", 2048):
        errs.append("ubatch > batch")
    if cfg.get("cache_type_v", "f16") not in ("f16", "bf16") and cfg.get("flash_attn") == "off":
        errs.append("quantized V cache needs flash_attn")
    if cfg.get("vk_disable_mmvq") and cfg.get("vk_force_mmvq"):
        errs.append("vk_disable_mmvq and vk_force_mmvq both set")
    if cfg.get("win_min_state", 0) > cfg.get("win_max_state", 100):
        errs.append("win_min_state > win_max_state")
    if cfg.get("smt") == "off":
        if cfg.get("threads", 1) > (os.cpu_count() or 2) // 2:
            errs.append("threads > physical cores with SMT off")
        if int(str(cfg.get("cpu_mask", "0x0")), 16) & _smt_sibling_mask():
            errs.append("cpu_mask uses offline SMT siblings")
    return errs


def table(knobs, only_enabled=False):
    rows = ["| name | file | layer | key | type | domain | default | flags |",
            "|---|---|---|---|---|---|---|---|"]
    for k in knobs.values():
        if only_enabled and not k.enabled:
            continue
        flags = " ".join(f for f, on in (("root", k.root), ("quality", k.quality), ("gpu", k.gpu),
                                         ("off-by-default", not k.enabled)) if on)
        rows.append(f"| {k.name} | {k.file} | {k.layer} | `{k.key}` | {k.type} | {k.domain()} "
                    f"| {k.default} | {flags} |")
    return "\n".join(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=P.PLATFORMS, default=None)
    ap.add_argument("--enabled", action="store_true")
    a = ap.parse_args()
    reg = load(a.platform)
    print(f"platform: {P.current.name} | llama: {P.current.llama} | model: {P.current.model}\n")
    print(table(reg, only_enabled=a.enabled))
