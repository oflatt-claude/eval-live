"""Benchmark callbacks and subprocess measurement helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Callable, Mapping, Sequence

from .store import Data, readonly_parameters, validate_tables


@dataclass
class ProcessResult:
    """Outcome of a command, including diagnostics for failed attempts."""

    argv: tuple[str, ...]
    cwd: str
    seconds: float
    returncode: int | None
    stdout: str
    stderr: str
    status: str


class ProcessError(RuntimeError):
    """A command failed or timed out; ``result`` retains its diagnostics."""

    def __init__(self, result: ProcessResult):
        self.result = result
        super().__init__(f"command {result.status} (exit {result.returncode}): {result.argv!r}")


class ProcessInterrupted(KeyboardInterrupt):
    """An interrupted process whose captured diagnostics can still be saved."""

    def __init__(self, result: ProcessResult):
        self.result = result


def run_command(argv: Sequence[str | Path], *, cwd: str | Path | None = None,
                timeout: float | None = None, env: Mapping[str, str] | None = None,
                check: bool = True) -> ProcessResult:
    """Measure a command without a shell, capturing stdout, stderr, and status.

    ``timeout=None`` means no deadline. ``env`` augments the current environment.
    A timeout or interrupt terminates the process group on POSIX, and the child
    on Windows. Nonzero exits and
    timeouts raise ``ProcessError`` when ``check`` is true. Use
    ``RunContext.command`` to also persist this information automatically.
    """
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be None or a finite positive number")
    args = tuple(os.fspath(value) for value in argv)
    if not args:
        raise ValueError("command must not be empty")
    started = time.perf_counter()
    process = subprocess.Popen(args, cwd=cwd, env={**os.environ, **(env or {})},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               start_new_session=os.name != "nt")
    status = "success"
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate()
        status = "interrupted" if isinstance(error, KeyboardInterrupt) else "timed-out"
    if process.returncode and status == "success":
        status = "failure"
    result = ProcessResult(args, str(Path(cwd or Path.cwd()).resolve()),
                           time.perf_counter() - started, process.returncode, stdout, stderr, status)
    if status == "interrupted":
        raise ProcessInterrupted(result)
    if check and status != "success":
        raise ProcessError(result)
    return result


@dataclass(frozen=True)
class Benchmark:
    """Collect one observation with ``run(context) -> named flat tables``.

    ``version`` is the explicit data format/measurement protocol version,
    stored as ``eval_runs.data_version`` and included in cache identity. Bump
    it when collector/build/helper code, measurement protocol, or returned tables
    change. Callback source is not inspected or hashed. Workload sources and
    data must be committed and read from ``context.directory``. Authors must pin
    dependencies and avoid modifying tracked files. ``environment`` records
    execution variables and ``tools`` records toolchain/dependency versions.

    Optional ``build(context) -> Path`` receives ``build_config`` and runs once
    per target/build identity. The resulting binary is ``context.binary`` in
    measurement callbacks. Use ``context.command`` to capture process details.

    Required ``error_rows(treatment, status)`` supplies named flat tables for
    pending, timed-out, failed, or interrupted treatments. It must be pure;
    all templates are validated before collection. Every declared table must
    have rows in both these templates and the collector's successful result.

    ``timeout`` is the optional per-command deadline in seconds (default None),
    overridden by ``--timeout``. Increasing it recollects timeouts while preserving
    successful observations. It does not preempt arbitrary Python callbacks.
    """

    name: str
    run: Callable[[RunContext], Data]
    version: str
    error_rows: Callable[[Mapping, str], Data]
    build: Callable[[RunContext], Path] | None = None
    build_config: Mapping = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    tools: Mapping = field(default_factory=dict)
    timeout: float | None = None

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name or not isinstance(self.version, str)
                or not self.version):
            raise ValueError("benchmark name and version are required")
        if self.timeout is not None and (type(self.timeout) not in (int, float)
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("timeout must be None or a finite positive number")
        if not callable(self.error_rows):
            raise ValueError("error_rows must be a function returning named flat tables")
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.environment.items()):
            raise ValueError("environment keys and values must be strings")
        for name in ("build_config", "tools", "environment"):
            object.__setattr__(self, name, readonly_parameters(getattr(self, name)))

    def fallback(self, config: Mapping, status: str) -> Data:
        """Construct explicit rows for an unfinished or unsuccessful treatment."""
        tables = self.error_rows(readonly_parameters(config), status)
        validate_tables(tables, user=True)
        if not tables or any(not rows for rows in tables.values()):
            raise ValueError("error_rows must supply at least one row in every declared table")
        return tables


@dataclass
class RunContext:
    """Inputs for one callback; commands run in ``directory`` by default."""

    directory: Path
    config: Mapping
    commit: str
    timeout: float | None
    environment: Mapping[str, str]
    binary: Path | None = None
    processes: list[ProcessResult] = field(default_factory=list)

    def __post_init__(self):
        self.config = readonly_parameters(self.config)
        self.environment = readonly_parameters(self.environment)

    def command(self, argv: Sequence[str | Path], *, cwd: str | Path | None = None,
                check: bool = True) -> ProcessResult:
        """Run and record a process using the benchmark's timeout/environment."""
        try:
            result = run_command(argv, cwd=cwd or self.directory, timeout=self.timeout,
                                 env=self.environment, check=False)
        except ProcessInterrupted as error:
            self.processes.append(error.result)
            raise
        self.processes.append(result)
        if check and result.status != "success":
            raise ProcessError(result)
        return result
