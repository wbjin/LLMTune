"""Platform selection: which params files are loaded and which llama binary runs.

    params/llama.toml        workload knobs, loaded on every platform
    params/<name>.toml       [platform] settings, [override.<knob>] tables, system knobs

Chosen with --platform (every run script) or $AUTOTUNE_PLATFORM; otherwise detected.
$LLAMA_BIN overrides the platform's llama binary.

Paths in [platform] may be Linux paths (`~` = Linux home) or Windows paths with %VARS%
(e.g. %USERPROFILE%\\llama\\llama.exe). Under WSL, Windows paths are resolved through
cmd.exe and translated with `wslpath -u`, so no user name is hardcoded in the params files.
"""
import os
import platform as _pf
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

PARAMS = Path(__file__).resolve().parent.parent / "params"
PLATFORMS = tuple(sorted(p.stem for p in PARAMS.glob("*.toml") if p.stem != "llama"))


def detect():
    if env := os.environ.get("AUTOTUNE_PLATFORM"):
        return env
    if os.name == "nt":
        return "windows"
    return "wsl" if "microsoft" in _pf.release().lower() else "linux-native"


@dataclass(frozen=True)
class Platform:
    name: str
    llama: str          # binary; `llama bench` must work
    model: str          # default model path ("" = HuggingFace cache)
    gpu: bool

    @property
    def windows_exe(self):
        """A Windows .exe launched from Linux (WSL interop): paths and env need translating."""
        return self.llama.lower().endswith(".exe") and os.name != "nt"

    def host_path(self, path):
        if not self.windows_exe:
            return path
        return subprocess.run(["wslpath", "-w", path], capture_output=True, text=True,
                              check=True).stdout.strip()


current: Platform | None = None


def read(name):
    path = PARAMS / f"{name}.toml"
    if name == "llama" or not path.exists():
        raise SystemExit(f"unknown platform {name!r}; choose from {', '.join(PLATFORMS)}")
    return tomllib.loads(path.read_text())


def _windows_var(name):
    """Value of a Windows environment variable, read from WSL through cmd.exe."""
    out = subprocess.run(["cmd.exe", "/c", f"echo %{name}%"], capture_output=True, text=True,
                         cwd="/mnt/c").stdout.strip()
    if not out or out == f"%{name}%":
        raise SystemExit(f"Windows environment variable %{name}% is not set")
    return out


def resolve_path(path):
    """Params-file path -> path usable by this Python process."""
    if not path:
        return path
    if os.name == "nt":
        return os.path.expandvars(path)
    if "%" in path or re.match(r"^[A-Za-z]:[\\/]", path):        # Windows path, used from WSL
        path = re.sub(r"%(\w+)%", lambda m: _windows_var(m.group(1)), path)
        return subprocess.run(["wslpath", "-u", path], capture_output=True, text=True,
                              check=True).stdout.strip()
    return os.path.expanduser(path)


def _hf_model():
    hits = sorted((Path.home() / ".cache/huggingface/hub").glob("models--*/snapshots/*/*.gguf"))
    return str(hits[0]) if hits else ""


def select(name=None):
    """Make `name` (default: detect()) the current platform and return it."""
    global current
    name = name or detect()
    s = read(name).get("platform", {})
    llama = os.environ.get("LLAMA_BIN") or resolve_path(s.get("llama", "llama"))
    model = resolve_path(s["model"]) if s.get("model") else _hf_model()
    current = Platform(name=name, llama=llama, model=model, gpu=s.get("gpu", True))
    return current


def get():
    return current or select()
