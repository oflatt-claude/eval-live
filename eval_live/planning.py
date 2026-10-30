"""Plan from artifact requirements, collect missing observations, then render."""
from __future__ import annotations

from argparse import Namespace
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
from pathlib import Path
import platform
import sys
import time
from typing import Mapping, Sequence
import uuid

from .artifacts import Artifact, Figure, Macros, Table
from .benchmarks import Benchmark, ProcessError, RunContext, callable_identity, file_digest
from .cli import CONTROL_ARGUMENTS
from .render import render_artifacts
from .store import Store, digest, parameter_rows, parameters, validate_tables
from .targets import Target, script_revision


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlannedRun:
    """One deduplicated execution request and its reusable observations."""

    key: str
    benchmark: Benchmark
    target: Target
    config: dict
    arguments: dict
    environment: dict
    inputs: list[dict]
    config_id: str
    environment_id: str
    runner_id: str
    samples: int
    selected: list[dict] = field(default_factory=list)
    needed_by: list[str] = field(default_factory=list)

    @property
    def missing(self) -> int:
        """Number of additional successful observations required."""
        return max(0, self.samples - len(self.selected))


@dataclass
class Plan:
    """Selected artifacts, shared run requests, and their named dependencies."""

    artifacts: list[Artifact]
    runs: dict[str, PlannedRun]
    requirements: dict[str, dict[str, list[tuple[str, int]]]]

    @property
    def missing(self) -> int:
        return sum(run.missing for run in self.runs.values())

    @property
    def reused(self) -> int:
        return sum(len(run.selected) for run in self.runs.values())

    def describe(self) -> str:
        """Explain required runs, cache hits, and the artifacts needing them."""
        lines = [f"{len(self.artifacts)} artifacts; {self.reused} cached samples; {self.missing} missing samples"]
        for run in self.runs.values():
            lines.append(f"  {run.benchmark.name}@{run.target.commit[:12]} {run.config!r}: "
                         f"{len(run.selected)} cached, {run.missing} missing; needed by {', '.join(run.needed_by)}")
        return "\n".join(lines)


@dataclass
class EvaluationResult:
    """Outcome, persisted invocation identity, and generated paths."""

    invocation_id: str | None
    data_path: Path
    collected: int
    reused: int
    written: list[Path]
    plan: Plan | None = None


class CollectionError(RuntimeError):
    """Required data is incomplete; completed observations remain in the store."""

    def __init__(self, errors: Sequence[str], result: EvaluationResult):
        self.errors = tuple(errors)
        self.result = result
        super().__init__("evaluation incomplete: " + "; ".join(errors))


class Evaluation:
    """A paper's benchmark definitions and ordered artifact catalog.

    ``run(args=namespace, argv=tokens)`` saves all arguments, plans selected
    artifacts, reuses exact observations, and writes paper outputs and HTML.
    Unknown CLI parameters affect measurement identity by default. Explicitly
    list selection-only parameters in ``selection_arguments``; their resolved
    values must still be expressed in individual requests where appropriate.

    ``browser_source`` can be a Path or source string containing top-level
    rendering callbacks. By default their common source file is used. Keep
    execution in a normal ``__main__`` guard so importing it only defines code.
    """

    def __init__(self, *, benchmarks: Sequence[Benchmark], artifacts: Sequence[Artifact],
                 selection_arguments: Sequence[str] = (), browser_source: str | Path | None = None,
                 title: str = "Paper evaluation"):
        self.benchmarks = {benchmark.name: benchmark for benchmark in benchmarks}
        if len(self.benchmarks) != len(benchmarks):
            raise ValueError("benchmark names must be unique")
        self.artifacts = list(artifacts)
        if not self.artifacts or any(type(a) not in (Figure, Table, Macros) for a in self.artifacts):
            raise ValueError("provide at least one Figure, Table, or Macros artifact")
        ids = [artifact.id.casefold() for artifact in self.artifacts]
        titles = [artifact.title or artifact.id for artifact in self.artifacts]
        if len(set(ids)) != len(ids) or len(set(titles)) != len(titles):
            raise ValueError("artifact IDs and display names must be unique")
        self.selection_arguments = set(selection_arguments)
        self.browser_source = browser_source
        self.title = title

    @staticmethod
    def _options(args: Namespace | Mapping | None) -> dict:
        supplied = vars(args) if isinstance(args, Namespace) else dict(args or {})
        options = dict(output="out", data=None, only=None, benchmarks=None, force=False,
                       dry_run=False, render_only=False, invocation=None, allow_dirty=False)
        options.update(parameters(supplied))
        if options["render_only"] and (options["force"] or options["dry_run"]):
            raise ValueError("--render-only cannot be combined with --force or --dry-run")
        if options["invocation"] and not options["render_only"]:
            raise ValueError("--invocation requires --render-only")
        return options

    def _artifacts(self, options: dict) -> list[Artifact]:
        names = options["only"]
        if names is None:
            return self.artifacts
        if not isinstance(names, list) or not names or len(set(names)) != len(names):
            raise ValueError("--only must select distinct artifact IDs")
        by_id = {artifact.id: artifact for artifact in self.artifacts}
        unknown = set(names) - by_id.keys()
        if unknown:
            raise ValueError(f"unknown artifacts: {sorted(unknown)}")
        return [by_id[name] for name in names]

    @staticmethod
    def _data_path(options: dict) -> Path:
        path = Path(options["data"] or Path(options["output"]) / "results.json").expanduser().resolve()
        if path.suffix.lower() != ".json":
            raise ValueError("the data/cache file must have a .json extension")
        return path

    def plan(self, *, args: Namespace | Mapping | None = None) -> Plan:
        """Resolve requirements and inspect the cache without builds or writes."""
        options = self._options(args)
        return self._plan(options, Store(self._data_path(options)))

    def _plan(self, options: dict, store: Store) -> Plan:
        artifacts = self._artifacts(options)
        measurement_args = {key: value for key, value in options.items()
                            if key not in CONTROL_ARGUMENTS and key not in self.selection_arguments}
        allowed = options["benchmarks"]
        if allowed is not None and (not isinstance(allowed, list) or set(allowed) - self.benchmarks.keys()):
            raise ValueError("--benchmarks contains unknown benchmark names")
        plan = Plan(artifacts, {}, {})
        targets = {}
        for artifact in artifacts:
            plan.requirements[artifact.id] = {}
            for name, requests in artifact.requires.items():
                requirements = {}
                for request in requests:
                    if request.benchmark not in self.benchmarks:
                        raise ValueError(f"unknown benchmark {request.benchmark!r} required by {artifact.id}")
                    if allowed is not None and request.benchmark not in allowed:
                        raise ValueError(f"benchmark subset excludes {request.benchmark!r}, required by {artifact.id}")
                    benchmark = self.benchmarks[request.benchmark]
                    target_key = (str(request.repository), request.commit)
                    if target_key not in targets:
                        targets[target_key] = Target.resolve(request.repository, request.commit,
                                                             allow_dirty=options["allow_dirty"])
                    target = targets[target_key]
                    inputs = target.input_hashes(benchmark.inputs)
                    environment = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                                   "platform": platform.platform(), "machine": platform.node(),
                                   "executable": sys.executable,
                                   **{f"env.{key}": value for key, value in benchmark.environment.items()},
                                   **{f"tool.{key}": value for key, value in parameters(benchmark.tools).items()}}
                    config = parameters(request.config)
                    config_id = digest({"request": config, "arguments": measurement_args,
                                        "build": parameters(benchmark.build_config)})
                    runner_id = benchmark.identity()
                    environment_id = digest(environment)
                    key = digest({"repository": target.identity, "commit": target.commit,
                                  "dirty": target.dirty, "benchmark": benchmark.name,
                                  "runner": runner_id, "config": config_id, "inputs": inputs,
                                  "environment": environment_id, "timeout": benchmark.timeout})
                    if key not in plan.runs:
                        plan.runs[key] = PlannedRun(key, benchmark, target, config, measurement_args,
                                                   environment, inputs, config_id, environment_id,
                                                   runner_id, request.samples)
                    run = plan.runs[key]
                    run.samples = max(run.samples, request.samples)
                    reason = f"{artifact.id}/{name}"
                    if reason not in run.needed_by:
                        run.needed_by.append(reason)
                    if not target.dirty and not options["force"]:
                        run.selected = store.cached(key)[-run.samples:]
                    requirements[key] = max(requirements.get(key, 0), request.samples)
                plan.requirements[artifact.id][name] = list(requirements.items())
        return plan

    def _invocation(self, store: Store, options: dict, argv: Sequence[str]) -> dict:
        for row in store.data["eval_invocations"]:
            if row["status"] == "running":
                row.update(status="interrupted", finished_at=now())
        for row in store.data["eval_runs"]:
            if row["status"] == "running":
                row.update(status="interrupted", finished_at=now(), error="previous writer stopped before completing this attempt")
        script = self.browser_source if isinstance(self.browser_source, Path) else None
        if script is None:
            filename = inspect.getsourcefile(self.artifacts[0].render)
            script = Path(filename).resolve() if filename else None
        invocation = dict(invocation_id=str(uuid.uuid4()), started_at=now(), finished_at=None,
                          status="running", cwd=str(Path.cwd()), python=platform.python_version(),
                          platform=platform.platform(), **script_revision(script))
        store.data["eval_invocations"].append(invocation)
        store.data["eval_arguments"].extend(parameter_rows(options, invocation_id=invocation["invocation_id"]))
        store.data["eval_argv"].extend(dict(invocation_id=invocation["invocation_id"], position=index, token=token)
                                       for index, token in enumerate(argv))
        store.save()
        return invocation

    @staticmethod
    def _register_run_metadata(store: Store, planned: PlannedRun) -> None:
        if not any(row["config_id"] == planned.config_id for row in store.data["eval_configs"]):
            for scope, values in (("request", planned.config), ("arguments", planned.arguments),
                                  ("build", planned.benchmark.build_config)):
                store.data["eval_configs"].extend(parameter_rows(values, config_id=planned.config_id, scope=scope))
        if not any(row["environment_id"] == planned.environment_id for row in store.data["eval_environments"]):
            store.data["eval_environments"].extend(parameter_rows(planned.environment, environment_id=planned.environment_id))

    def _collect(self, store: Store, plan: Plan, invocation: dict) -> tuple[int, list[str]]:
        errors = []
        collected = 0
        with ExitStack() as stack:
            directories = {}
            binaries = {}
            for planned in plan.runs.values():
                if not planned.missing:
                    continue
                self._register_run_metadata(store, planned)
                benchmark, target = planned.benchmark, planned.target
                for _ in range(planned.missing):
                    sample = max((r["sample"] for r in store.data["eval_runs"] if r["cache_key"] == planned.key), default=-1) + 1
                    row = dict(run_id=str(uuid.uuid4()), invocation_id=invocation["invocation_id"],
                               cache_key=planned.key, cacheable=not target.dirty, benchmark=benchmark.name,
                               repository=target.identity, requested_ref=target.ref, commit=target.commit,
                               dirty=target.dirty, runner_id=planned.runner_id, config_id=planned.config_id,
                               environment_id=planned.environment_id, sample=sample,
                               timeout=benchmark.timeout, started_at=now(), finished_at=None,
                               status="running", seconds=None, binary_sha256=None, error=None)
                    store.data["eval_runs"].append(row)
                    store.data["eval_inputs"].extend(dict(item, run_id=row["run_id"]) for item in planned.inputs)
                    store.save()
                    contexts = []
                    started = time.perf_counter()
                    try:
                        target_key = (target.identity, target.commit, target.working)
                        if target_key not in directories:
                            directories[target_key] = stack.enter_context(target.checkout())
                        directory = directories[target_key]
                        if target.input_hashes(benchmark.inputs) != planned.inputs:
                            raise ValueError("declared inputs changed after planning")
                        binary = None
                        if benchmark.build:
                            build_key = digest([*target_key, callable_identity(benchmark.build),
                                                parameters(benchmark.build_config), planned.environment_id,
                                                planned.runner_id, planned.inputs])
                            if build_key not in binaries:
                                build_context = RunContext(directory, parameters(benchmark.build_config), sample,
                                                           target.commit, benchmark.timeout, benchmark.environment)
                                contexts.append(build_context)
                                binary = Path(benchmark.build(build_context))
                                binary = binary if binary.is_absolute() else directory / binary
                                if not binary.is_file():
                                    raise ValueError("build callback did not produce a file")
                                binaries[build_key] = binary
                            binary = binaries[build_key]
                            row["binary_sha256"] = file_digest(binary)
                        context = RunContext(directory, planned.config, sample, target.commit,
                                             benchmark.timeout, benchmark.environment, binary)
                        contexts.append(context)
                        if target.materialized_input_hashes(directory, benchmark.inputs) != planned.inputs:
                            raise ValueError("declared inputs changed while preparing the target")
                        tables = benchmark.run(context)
                        validate_tables(tables, user=True)
                        if target.materialized_input_hashes(directory, benchmark.inputs) != planned.inputs:
                            raise ValueError("declared inputs changed during collection")
                        for name, rows in tables.items():
                            store.data.setdefault(name, []).extend(dict(item, run_id=row["run_id"]) for item in rows)
                        row["status"] = "success"
                        planned.selected.append(row)
                        collected += 1
                    except KeyboardInterrupt:
                        row.update(status="interrupted", error="interrupted by user")
                        raise
                    except Exception as error:
                        row.update(status=error.result.status if isinstance(error, ProcessError) else "failure", error=str(error))
                        errors.append(f"{benchmark.name}@{target.commit[:12]}: {error}")
                    finally:
                        row.update(finished_at=now(), seconds=time.perf_counter() - started)
                        for context in contexts:
                            for process in context.processes:
                                process_id = str(uuid.uuid4())
                                store.data["eval_processes"].append(dict(process_id=process_id, run_id=row["run_id"],
                                    cwd=process.cwd, seconds=process.seconds, returncode=process.returncode,
                                    stdout=process.stdout, stderr=process.stderr, status=process.status))
                                store.data["eval_commands"].extend(dict(process_id=process_id, run_id=row["run_id"],
                                    position=i, value=value) for i, value in enumerate(process.argv))
                        store.save()
                    if row["status"] != "success":
                        break
        return collected, errors

    @staticmethod
    def _selections(store: Store, plan: Plan, invocation_id: str) -> tuple[list[Artifact], dict]:
        ready = []
        selections = {}
        for artifact in plan.artifacts:
            requirements = plan.requirements[artifact.id]
            if any(len(plan.runs[key].selected) < count for requests in requirements.values() for key, count in requests):
                continue
            ready.append(artifact)
            selections[artifact.id] = {}
            for name, requests in requirements.items():
                ids = list(dict.fromkeys(row["run_id"] for key, count in requests for row in plan.runs[key].selected[-count:]))
                selections[artifact.id][name] = ids
                store.data["eval_selections"].extend(dict(invocation_id=invocation_id,
                    artifact_id=artifact.id, requirement=name, position=i, run_id=run_id)
                    for i, run_id in enumerate(ids))
        return ready, selections

    def _render_only(self, store: Store, options: dict, argv: Sequence[str]) -> EvaluationResult:
        artifacts = self._artifacts(options)
        saved = options["invocation"]
        if saved is None:
            candidates = [row for row in store.data["eval_invocations"] if row["status"] == "success"]
            if not candidates:
                raise ValueError("no completed invocation to render")
            saved = candidates[-1]["invocation_id"]
        elif not any(row["invocation_id"] == saved for row in store.data["eval_invocations"]):
            raise ValueError(f"unknown saved invocation: {saved}")
        selections = {}
        for artifact in artifacts:
            rows = [row for row in store.data["eval_selections"]
                    if row["invocation_id"] == saved and row["artifact_id"] == artifact.id]
            if {row["requirement"] for row in rows} != set(artifact.requires):
                raise ValueError(f"saved invocation is missing requirements for {artifact.id}")
            selections[artifact.id] = {name: [row["run_id"] for row in sorted(rows, key=lambda r: r["position"])
                                            if row["requirement"] == name] for name in artifact.requires}
        invocation = self._invocation(store, options, argv)
        invocation["source_invocation"] = saved
        for artifact_id, scopes in selections.items():
            for name, ids in scopes.items():
                store.data["eval_selections"].extend(dict(invocation_id=invocation["invocation_id"],
                    artifact_id=artifact_id, requirement=name, position=i, run_id=run_id)
                    for i, run_id in enumerate(ids))
        store.save()
        try:
            invocation.update(status="success", finished_at=now())
            written = render_artifacts(store.data, artifacts, selections, Path(options["output"]).expanduser(),
                                       source=self.browser_source, title=self.title)
        except BaseException:
            invocation["status"] = "failure"
            raise
        finally:
            if invocation["finished_at"] is None:
                invocation["finished_at"] = now()
            store.save()
        return EvaluationResult(invocation["invocation_id"], store.path, 0,
                                len({run_id for scopes in selections.values() for ids in scopes.values() for run_id in ids}), written)

    def run(self, *, args: Namespace | Mapping | None = None, argv: Sequence[str] | None = None) -> EvaluationResult:
        """Plan, collect, checkpoint, and render the selected paper outputs.

        Default: all artifacts, reusable observations from ``out/results.json``.
        ``--dry-run`` returns a plan without writing. ``--render-only`` uses
        saved sample selections without resolving Git or invoking benchmarks.
        Incomplete collection raises ``CollectionError`` after saving failures
        and rendering independent artifacts whose requirements are complete.
        """
        options = self._options(args)
        tokens = list(sys.argv[1:] if argv is None else argv)
        store = Store(self._data_path(options))
        if options["dry_run"]:
            plan = self._plan(options, store)
            print(plan.describe())
            return EvaluationResult(None, store.path, 0, plan.reused, [], plan)
        with store.writer():
            if options["render_only"]:
                return self._render_only(store, options, tokens)
            plan = self._plan(options, store)
            print(plan.describe())
            reused = plan.reused
            invocation = self._invocation(store, options, tokens)
            try:
                collected, errors = self._collect(store, plan, invocation)
                ready, selections = self._selections(store, plan, invocation["invocation_id"])
                invocation.update(status="failure" if errors else "success", finished_at=now())
                store.save()
                written = render_artifacts(store.data, ready, selections, Path(options["output"]).expanduser(),
                                           source=self.browser_source, title=self.title)
                result = EvaluationResult(invocation["invocation_id"], store.path, collected, reused, written, plan)
                if errors:
                    raise CollectionError(errors, result)
                return result
            except KeyboardInterrupt:
                invocation["status"] = "interrupted"
                raise
            except BaseException:
                invocation["status"] = "failure"
                raise
            finally:
                if invocation["finished_at"] is None:
                    invocation["finished_at"] = now()
                store.save()
