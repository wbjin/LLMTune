"""PostgreSQL 14 benchmark helpers. Importing this module has no side effects."""

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import time

SPACE = {
    "shared_buffers": ["128MB", "1GB", "2GB", "4GB", "8GB", "16GB"],
    "max_wal_size": ["1GB", "2GB", "4GB", "8GB"],
    "wal_compression": ["off", "on"],
}
EXPANDED_SPACE = {
    **SPACE,
    "shared_buffers": ["128MB", "256MB", "512MB", "1GB", "2GB", "4GB", "8GB", "16GB"],
    # commit_delay is in microseconds; commit_siblings is concurrent transactions.
    "commit_delay": ["0", "10", "50", "100"],
    "commit_siblings": ["1", "5", "10"],
}
SEARCH_SPACES = {"original": SPACE, "expanded": EXPANDED_SPACE}

CONNECTION = ["-h", "/users/wbjin/pgsocket", "-p", "5432", "-U", "wbjin"]
PG_CTL = "/usr/lib/postgresql/14/bin/pg_ctl"
PG_DATA = Path("/users/wbjin/pgdata")
PG_LOG = "/users/wbjin/postgres.log"
DATABASE = "pgbench_opentuner"


def positive(value):
    value = int(value)
    if value <= 0:
        raise ValueError("must be positive")
    return value


class PostgresBenchmark:
    database = DATABASE

    def __init__(self, output, duration=600, warmup=120, repeats=3, scale=100,
                 reuse_dataset=False, search_space="original"):
        self.output = Path(output).resolve()
        self.duration, self.warmup = duration, warmup
        self.repeats, self.scale = repeats, scale
        self.reuse_dataset = reuse_dataset
        self.space = SEARCH_SPACES[search_space]
        self.sequence = 0
        self.environment = {
            k: v for k, v in os.environ.items() if not k.startswith("PG")
        }
        self.environment["LC_ALL"] = "C"

    def save(self, path, value):
        path.write_text(json.dumps(value, indent=2) + "\n")

    def command(self, argv, postgres=False, timeout=120):
        # Database commands run as the current OS user.
        self.sequence += 1
        log = self.output / f"command-{self.sequence:05d}.log"
        with log.open("w") as handle:
            handle.write(json.dumps(argv) + "\n")
            handle.flush()
            process = subprocess.Popen(
                argv, stdout=handle, stderr=subprocess.STDOUT,
                env=self.environment, cwd="/tmp", start_new_session=True,
            )
            try:
                code = process.wait(timeout=timeout)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
        if code:
            raise RuntimeError(f"Command exited {code}; see {log}")
        return log.read_text().split("\n", 1)[1].strip()

    def sql(self, query):
        return self.command(
            ["psql", *CONNECTION, "-X", "-At", "-v", "ON_ERROR_STOP=1",
             "-d", "postgres", "-c", query],
            postgres=True,
        )

    def settings(self):
        return json.loads(self.sql(
            "SELECT json_object_agg(name, current_setting(name)) "
            "FROM pg_settings"
        ))

    def restart(self):
        self.command(
            [PG_CTL, "-D", str(PG_DATA), "-l", PG_LOG,
             "-m", "fast", "-w", "restart"], timeout=180,
        )
        self.command(["pg_isready", *CONNECTION], postgres=True)

    def check_space(self):
        for path in (self.data, (self.data / "pg_wal").resolve()):
            if shutil.disk_usage(path).free < 20 * 1024**3:
                raise RuntimeError(f"Need at least 20 GiB free on {path}")

    @contextmanager
    def session(self):
        if os.geteuid() == 0:
            raise RuntimeError("Run as wbjin, without sudo")
        if PG_DATA.stat().st_uid != os.geteuid():
            raise RuntimeError("Run as the owner of the PostgreSQL data directory")
        with open(PG_DATA / "tuning.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
            initial = self.settings()
            self.data = Path(initial["data_directory"])
            if (not initial["server_version"].startswith("14.")
                    or self.data != PG_DATA):
                raise RuntimeError("Expected the wbjin-owned PostgreSQL 14 instance")
            for name in ("fsync", "synchronous_commit", "full_page_writes",
                         "autovacuum"):
                if initial[name] != "on":
                    raise RuntimeError(f"{name} must be on")
            if self.sql(
                "SELECT EXISTS (SELECT 1 FROM pg_settings WHERE pending_restart)"
            ) == "t":
                raise RuntimeError("Resolve pending restarts before tuning")
            self.check_space()
            self.save(self.output / "initial-settings.json", initial)
            for command in (["lscpu"], ["free", "-h"],
                            ["df", "-h", str(self.data)]):
                self.command(command)
            auto = self.data / "postgresql.auto.conf"
            backup = self.output / "postgresql.auto.conf.original"
            original_stat = auto.stat() if auto.exists() else None
            if original_stat:
                shutil.copy2(auto, backup)
            self.save(self.output / "recovery.json", {
                "target": str(auto), "backup": str(backup),
                "originally_existed": original_stat is not None,
                "instructions": (
                    "If terminated without cleanup, restore the original file "
                    "(or remove it if originally absent), preserving wbjin "
                    f"ownership, then run {PG_CTL} -D {PG_DATA} "
                    f"-l {PG_LOG} -w restart."
                ),
            })
            try:
                if self.sql(
                    f"SELECT 1 FROM pg_database WHERE datname = '{self.database}'"
                ) != "1":
                    self.command(["createdb", *CONNECTION, self.database], True)
                if self.reuse_dataset:
                    print("Initializing dataset once for fast search...", flush=True)
                    self.command(
                        ["pgbench", *CONNECTION, "-i", "-s",
                         str(self.scale), self.database],
                        True, timeout=1800,
                    )
                yield self
            finally:
                if original_stat:
                    temporary = auto.with_name("postgresql.auto.conf.tuning-restore")
                    shutil.copy2(backup, temporary)
                    os.replace(temporary, auto)
                else:
                    auto.unlink(missing_ok=True)
                self.restart()
                restored = self.settings()
                for name in self.space:
                    if restored[name] != initial[name]:
                        raise RuntimeError(f"Failed to restore {name}")
                print("Original PostgreSQL settings restored.", flush=True)

    def snapshot(self):
        return json.loads(self.sql(f"""
            SELECT json_build_object(
              'bgwriter', (SELECT row_to_json(s) FROM pg_stat_bgwriter s),
              'wal', (SELECT row_to_json(s) FROM pg_stat_wal s),
              'database', (SELECT row_to_json(s) FROM pg_stat_database s
                           WHERE datname = '{self.database}'));
        """))

    def measure(self, argv, timeout, trial, repetition):
        """Measured phase only; a profiler can wrap this without changing setup or warm-up."""
        return self.command(argv, True, timeout=timeout)

    def evaluate(self, config, label):
        if set(config) != set(self.space):
            raise ValueError("Unexpected configuration keys")
        for name, choices in self.space.items():
            if config[name] not in choices:
                raise ValueError(f"Unsupported value for {name}")
        trial = self.output / label
        trial.mkdir()
        self.save(trial / "config.json", config)
        started = time.monotonic()
        try:
            self.check_space()
            for name, value in config.items():
                self.sql(f"ALTER SYSTEM SET {name} = '{value}'")
            self.restart()
            effective = self.settings()
            self.save(trial / "effective-settings.json", effective)
            if any(effective[name] != value for name, value in config.items()):
                raise RuntimeError("Effective configuration does not match candidate")
            measurements = []
            command = ["pgbench", *CONNECTION, "-c", "16", "-j", "4",
                       "-M", "simple", "-P", "5"]
            for repetition in range(1, self.repeats + 1):
                self.check_space()
                if not self.reuse_dataset:
                    self.command(
                        ["pgbench", *CONNECTION, "-i", "-s",
                         str(self.scale), self.database],
                        True, timeout=1800,
                    )
                self.command(
                    [*command, "-T", str(self.warmup), self.database],
                    True, timeout=self.warmup + 120,
                )
                before = self.snapshot()
                output = self.measure(
                    [*command, "-T", str(self.duration), self.database],
                    self.duration + 120, trial, repetition,
                )
                tps = re.search(r"^tps = ([\d.]+)", output, re.M)
                latency = re.search(r"^latency average = ([\d.]+)", output, re.M)
                if not tps or not latency:
                    raise RuntimeError("Missing pgbench result metrics")
                tps, latency = float(tps[1]), float(latency[1])
                if not math.isfinite(tps) or tps <= 0:
                    raise RuntimeError("Invalid throughput")
                result = {"tps": tps, "latency_ms": latency,
                          "before": before, "after": self.snapshot()}
                measurements.append(result)
                self.save(trial / f"repetition-{repetition}.json", result)
                print(f"{label}, repetition {repetition}: {tps:.1f} TPS", flush=True)
            result = {
                "median_tps": statistics.median(r["tps"] for r in measurements),
                "measurements": measurements,
                "elapsed_seconds": time.monotonic() - started,
            }
            self.save(trial / "result.json", result)
            return result
        except Exception as error:
            self.save(trial / "failure.json", {"error": str(error)})
            raise
