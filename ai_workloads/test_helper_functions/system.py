"""System layer: snapshot host state (read-only) and apply/restore system knobs.

Two system layers:
    sysfs     Linux /sys and /proc files, written through `sudo -n tee`
    powercfg  Windows power plan (active scheme, AC values), via powercfg(.exe); works from WSL

snapshot() is always safe and is saved with every run so results are
reproducible. apply()/restore() are only used when a tuner is started with --allow-system.
"""
import glob
import platform
import re
import shutil
import subprocess
from pathlib import Path

from . import platforms as P
from .knobs import SYSTEM_LAYERS

POWERCFG = shutil.which("powercfg.exe") or shutil.which("powercfg")


def _read(path):
    try:
        text = Path(path).read_text().strip()
    except OSError:
        return None
    m = re.search(r"\[([^\]]+)\]", text)      # "always [madvise] never" -> "madvise"
    return m.group(1) if m else text


def _powercfg_read(knob):
    if not POWERCFG:
        return None
    out = _cmd(POWERCFG, "/qh", "SCHEME_CURRENT", *knob.key.split()) or ""
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    if not m:
        return None
    v = int(m.group(1), 16)
    return v if knob.type == "int" else str(v)     # enum/bool compare as strings, like sysfs


def read_knob(knob):
    if knob.layer == "powercfg":
        return _powercfg_read(knob)
    vals = {_read(p) for p in sorted(glob.glob(knob.key))}
    vals.discard(None)
    if not vals:
        return None
    return vals.pop() if len(vals) == 1 else sorted(vals)   # list = CPUs disagree


def _cmd(*argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _on_ac_power():
    """True/False on machines with a power supply class; None if there is none (desktop, WSL)."""
    supplies = glob.glob("/sys/class/power_supply/A*/online")
    return _read(supplies[0]) == "1" if supplies else None


def snapshot(knobs):
    plat = P.get()
    thermal = {}
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        t = _read(f"{zone}/temp")
        if t and t.lstrip("-").isdigit():
            thermal[_read(f"{zone}/type")] = int(t) / 1000
    return {
        "platform": plat.name,
        "host": platform.node(),
        "kernel": platform.release(),
        "cpu": _cmd("sh", "-c", "lscpu | sed -n 's/^Model name: *//p'"),
        "llama_bin": plat.llama,
        "llama": _cmd(plat.llama, "--version"),
        "devices": _cmd(plat.llama, "bench", "--list-devices"),
        "on_ac_power": _on_ac_power(),
        "loadavg": _read("/proc/loadavg"),
        "thermal_c": thermal,
        "system": {k.name: read_knob(k) for k in knobs.values() if k.layer in SYSTEM_LAYERS},
    }


def _write(path, value):
    p = subprocess.run(["sudo", "-n", "tee", path], input=str(value), text=True,
                       capture_output=True)
    if p.returncode != 0:
        raise PermissionError(f"write {value!r} -> {path}: {p.stderr.strip()}")


def _powercfg(*args):
    p = subprocess.run([POWERCFG, *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise PermissionError(f"powercfg {' '.join(args)}: {(p.stdout + p.stderr).strip()}")


def apply(cfg, knobs, dry_run=False):
    """Write system knobs in registry order. Returns the list of writes."""
    writes, power = [], False
    for name, k in knobs.items():
        if name not in cfg or k.layer not in SYSTEM_LAYERS:
            continue
        value = cfg[name]
        if k.type == "bool":      # snapshots read back "0"/"1" strings
            value = int(value in (True, 1, "1"))
        if k.layer == "powercfg":
            if not POWERCFG:
                raise RuntimeError(f"{name}: powercfg not found")
            writes.append((f"powercfg {k.key}", int(value)))
            if not dry_run:
                _powercfg("/setacvalueindex", "SCHEME_CURRENT", *k.key.split(), str(int(value)))
            power = True
            continue
        for path in sorted(glob.glob(k.key)):
            writes.append((path, value))
            if not dry_run:
                _write(path, value)
    if power and not dry_run:
        _powercfg("/setactive", "SCHEME_CURRENT")     # re-apply the scheme so changes take effect
    return writes


def restore(snap, knobs, names, dry_run=False):
    """Write back the snapshotted value of each knob in `names` (the ones a tuner touched)."""
    cfg = {n: v for n, v in snap["system"].items()
           if n in names and v is not None and not isinstance(v, list)}
    return apply(cfg, knobs, dry_run)
