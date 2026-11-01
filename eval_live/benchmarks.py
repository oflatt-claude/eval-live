"""Benchmark callbacks and subprocess measurement helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .store import Data, digest, parameters


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
                timeout: float = 300, env: Mapping[str, str] | None = None,
                check: bool = True) -> ProcessResult:
    """Measure a command without a shell, capturing stdout, stderr, and status.

    ``env`` augments the current environment. A timeout or interrupt terminates
    the process group on POSIX, and the child on Windows. Nonzero exits and
    timeouts raise ``ProcessError`` when ``check`` is true. Use
    ``RunContext.command`` to also persist this information automatically.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive")
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


def callable_identity(function: Callable) -> str:
    """Hash a callback's source. Opaque callables require an explicit wrapper."""
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError) as error:
        raise ValueError("benchmark callbacks must have inspectable Python source") from error
    return digest({"name": function.__qualname__, "source": source})


@dataclass(frozen=True)
class Benchmark:
    """Collect one observation with ``run(context) -> named flat tables``.

    ``version`` is an explicit measurement protocol version. Declare helper
    functions in ``dependencies`` and input paths in ``inputs`` so changes
    invalidate the cache. Relative input paths refer to the benchmarked commit;
    absolute paths refer to external files. ``environment`` records execution
    variables and ``tools`` records toolchain/dependency versions.

    Optional ``build(context) -> Path`` receives ``build_config`` and runs once
    per target/build identity. The resulting binary is ``context.binary`` in
    measurement callbacks. Use ``context.command`` to capture process details.

    ``timeout`` is the per-command deadline in seconds, overridden by the CLI
    ``--timeout`` option. Increasing it retries cached timeouts while preserving
    successful observations. It does not preempt arbitrary Python callbacks.
    """

    name: str
    run: Callable[[RunContext], Data]
    version: str
    inputs: Sequence[str | Path] = ()
    dependencies: Sequence[Callable] = ()
    build: Callable[[RunContext], Path] | None = None
    build_config: Mapping = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    tools: Mapping = field(default_factory=dict)
    timeout: float = 300

    def __post_init__(self):
        if (not self.name or not self.version or type(self.timeout) not in (int, float)
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("benchmark name/version and a positive timeout are required")
        parameters(self.build_config)
        parameters(self.tools)
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in self.environment.items()):
            raise ValueError("environment keys and values must be strings")
        for name in ("build_config", "tools", "environment"):
            values = parameters(getattr(self, name))
            object.__setattr__(self, name, MappingProxyType({
                key: tuple(value) if isinstance(value, list) else value for key, value in values.items()
            }))
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))

    def identity(self) -> str:
        """Fingerprint measurement and declared helper code independently of plots."""
        return digest({"version": self.version, "run": callable_identity(self.run),
                       "helpers": [callable_identity(fn) for fn in self.dependencies],
                       "build": callable_identity(self.build) if self.build else None,
                       "build_config": parameters(self.build_config)})


@dataclass
class RunContext:
    """Inputs for one callback; commands run in ``directory`` by default."""

    directory: Path
    config: Mapping
    sample: int
    commit: str
    timeout: float
    environment: Mapping[str, str]
    binary: Path | None = None
    processes: list[ProcessResult] = field(default_factory=list)

    def __post_init__(self):
        self.config = MappingProxyType({
            key: tuple(value) if isinstance(value, list) else value
            for key, value in parameters(self.config).items()
        })
        self.environment = MappingProxyType(dict(self.environment))

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


def file_digest(path: Path) -> str:
    """Hash a file without loading the whole file into memory."""
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()
