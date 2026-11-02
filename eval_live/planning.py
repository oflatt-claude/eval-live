"""Plan from artifact requirements, collect missing observations, then render."""
from __future__ import annotations

from argparse import Namespace
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import inspect
import math
from pathlib import Path
import platform
import sys
import time
from types import MappingProxyType
from typing import Mapping, Sequence
import uuid

from .eval_live import Registry, _Output
from .benchmarks import Benchmark, ProcessError, RunContext, callable_identity, file_digest
from .cli import CONTROL_ARGUMENTS
from .render import render_artifacts
from .store import Store, digest, parameter_rows, parameters, validate_tables
from .targets import Target, script_revision


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlannedRun:
    """One deduplicated treatment and its applicable cached attempt."""

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
    selected: dict | None = None
    needed_by: list[str] = field(default_factory=list)
    blocked: dict | None = None

    @property
    def missing(self) -> int:
        """Number of additional observations eligible for collection."""
        return int(self.selected is None and self.blocked is None)


@dataclass
class Plan:
    """Selected outputs, shared treatment runs, and their named dependencies."""

    artifacts: list[_Output]
    runs: dict[str, PlannedRun]
    requirements: dict[str, dict[str, list[str]]]

    @property
    def missing(self) -> int:
        return sum(run.missing for run in self.runs.values())

    @property
    def reused(self) -> int:
        return sum(run.selected is not None for run in self.runs.values())

    def describe(self) -> str:
        """Explain required runs, cache hits, and the artifacts needing them."""
        lines = [f"{len(self.artifacts)} artifacts; {self.reused} cached treatments; {self.missing} missing treatments"]
        for run in self.runs.values():
            blocked = ""
            if run.blocked:
                remedy = "increase --timeout or use --force" if run.blocked["status"] == "timed-out" else "use --force"
                blocked = f"; blocked by cached {run.blocked['status']} ({remedy})"
            lines.append(f"  {run.benchmark.name}@{run.target.commit[:12]} {run.config!r}: "
                         f"{int(run.selected is not None)} cached, {run.missing} missing; timeout {run.benchmark.timeout:g}s; "
                         f"needed by {', '.join(run.needed_by)}{blocked}")
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


class _Runner:
    """Local collection engine for a Registry; never imported in the browser."""

    def __init__(self, registry: Registry):
        self.declarations = registry._benchmarks
        self.benchmarks = {name: Benchmark(**spec["benchmark"]) for name, spec in self.declarations.items()}
        self.artifacts = registry._outputs
        if not self.artifacts:
            raise ValueError("register at least one graph, table, or macros output")
        ids = [artifact.id.casefold() for artifact in self.artifacts]
        titles = [artifact.title for artifact in self.artifacts]
        if len(set(ids)) != len(ids) or len(set(titles)) != len(titles):
            raise ValueError("output IDs and display names must be unique")
        self.selection_arguments = registry.selection_arguments
        self.browser_source = registry.browser_source
        self.title = registry.title

    @staticmethod
    def _options(args: Namespace | Mapping | None) -> dict:
        supplied = vars(args) if isinstance(args, Namespace) else dict(args or {})
        options = dict(output="out", data=None, only=None, benchmarks=None, force=False,
                       dry_run=False, render_only=False, invocation=None, allow_dirty=False, timeout=None,
                       commit="HEAD")
        options.update(parameters(supplied))
        timeout = options["timeout"]
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be a finite positive number of seconds")
        if options["render_only"] and (options["force"] or options["dry_run"]):
            raise ValueError("--render-only cannot be combined with --force or --dry-run")
        if options["invocation"] and not options["render_only"]:
            raise ValueError("--invocation requires --render-only")
        return options

    def _artifacts(self, options: dict) -> list[_Output]:
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

    def _catalog(self, options: dict) -> dict[str, Mapping]:
        catalog = {}
        for name, spec in self.declarations.items():
            for raw in spec["treatments"]:
                config = parameters(raw)
                if "benchmark" in config:
                    raise ValueError("benchmark is a reserved treatment field")
                treatment = {"benchmark": name, "repository": spec["repository"],
                             "commit": spec["commit"] or options["commit"], **config}
                if any(not isinstance(treatment[key], str) or not treatment[key] for key in ("repository", "commit")):
                    raise ValueError("treatment repository and commit must be nonempty strings")
                catalog[digest(treatment)] = MappingProxyType({
                    key: tuple(value) if isinstance(value, list) else value for key, value in treatment.items()
                })
        return catalog

    @staticmethod
    def _choose(selector, catalog: dict, label: str) -> list[Mapping]:
        selected = {}
        try:
            rows = tuple(catalog.values())
            for row in rows if selector is None else selector(rows):
                key = digest(parameters(row))
                if key not in catalog:
                    raise ValueError("selector returned a treatment outside the declared catalog")
                selected[key] = catalog[key]
        except Exception as error:
            raise ValueError(f"invalid treatment selection for {label}: {error}") from error
        return list(selected.values())

    def _plan(self, options: dict, store: Store) -> Plan:
        artifacts = self._artifacts(options)
        measurement_args = {key: value for key, value in options.items()
                            if key not in CONTROL_ARGUMENTS and key not in self.selection_arguments}
        allowed = options["benchmarks"]
        if allowed is not None and (not isinstance(allowed, list) or set(allowed) - self.benchmarks.keys()):
            raise ValueError("--benchmarks contains unknown benchmark names")
        plan = Plan(artifacts, {}, {})
        catalog = self._catalog(options)
        targets = {}
        for artifact in artifacts:
            plan.requirements[artifact.id] = {}
            for name, selector in artifact.requires.items():
                requirements = []
                for treatment in self._choose(selector, catalog, f"{artifact.id}/{name}"):
                    benchmark_name = treatment["benchmark"]
                    if allowed is not None and benchmark_name not in allowed:
                        raise ValueError(f"benchmark subset excludes {benchmark_name!r}, required by {artifact.id}")
                    benchmark = self.benchmarks[benchmark_name]
                    if options["timeout"] is not None:
                        benchmark = replace(benchmark, timeout=options["timeout"])
                    target_key = (treatment["repository"], treatment["commit"])
                    if target_key not in targets:
                        targets[target_key] = Target.resolve(*target_key,
                                                             allow_dirty=options["allow_dirty"])
                    target = targets[target_key]
                    inputs = target.input_hashes(benchmark.inputs)
                    environment = {"python": platform.python_version(), "implementation": platform.python_implementation(),
                                   "platform": platform.platform(), "machine": platform.node(),
                                   "executable": sys.executable,
                                   **{f"env.{key}": value for key, value in benchmark.environment.items()},
                                   **{f"tool.{key}": value for key, value in parameters(benchmark.tools).items()}}
                    config = parameters({key: value for key, value in treatment.items()
                                         if key not in ("benchmark", "repository", "commit")})
                    config_id = digest({"request": config, "arguments": measurement_args,
                                        "build": parameters(benchmark.build_config)})
                    runner_id = benchmark.identity()
                    environment_id = digest(environment)
                    key = digest({"repository": target.identity, "commit": target.commit,
                                  "dirty": target.dirty, "benchmark": benchmark.name,
                                  "runner": runner_id, "config": config_id, "inputs": inputs,
                                  "environment": environment_id})
                    if key not in plan.runs:
                        plan.runs[key] = PlannedRun(key, benchmark, target, config, measurement_args,
                                                   environment, inputs, config_id, environment_id,
                                                   runner_id)
                    run = plan.runs[key]
                    reason = f"{artifact.id}/{name}"
                    if reason not in run.needed_by:
                        run.needed_by.append(reason)
                    if not target.dirty and not options["force"]:
                        cached = store.cached(key, timeout=benchmark.timeout)
                        latest = cached[-1] if cached else None
                        run.selected = latest if latest and latest["status"] == "success" else None
                        run.blocked = latest if latest and latest["status"] != "success" else None
                    if key not in requirements:
                        requirements.append(key)
                plan.requirements[artifact.id][name] = requirements
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
                if planned.blocked:
                    prior = planned.blocked
                    remedy = "increase --timeout or use --force" if prior["status"] == "timed-out" else "use --force to retry"
                    errors.append(f"{planned.benchmark.name}@{planned.target.commit[:12]}: cached {prior['status']} "
                                  f"at {prior['timeout']:g}s; {remedy}. {prior['error'] or ''}")
                    continue
                if not planned.missing:
                    continue
                self._register_run_metadata(store, planned)
                benchmark, target = planned.benchmark, planned.target
                row = dict(run_id=str(uuid.uuid4()), invocation_id=invocation["invocation_id"],
                           cache_key=planned.key, cacheable=not target.dirty, benchmark=benchmark.name,
                           repository=target.identity, requested_ref=target.ref, commit=target.commit,
                           dirty=target.dirty, runner_id=planned.runner_id, data_version=benchmark.version, config_id=planned.config_id,
                           environment_id=planned.environment_id,
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
                            build_context = RunContext(directory, parameters(benchmark.build_config),
                                                       target.commit, benchmark.timeout, benchmark.environment)
                            contexts.append(build_context)
                            binary = Path(benchmark.build(build_context))
                            binary = binary if binary.is_absolute() else directory / binary
                            if not binary.is_file():
                                raise ValueError("build callback did not produce a file")
                            binaries[build_key] = binary
                        binary = binaries[build_key]
                        row["binary_sha256"] = file_digest(binary)
                    context = RunContext(directory, planned.config, target.commit,
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
                    planned.selected = row
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
        return collected, errors

    @staticmethod
    def _selections(store: Store, plan: Plan, invocation_id: str) -> tuple[list[_Output], dict]:
        ready = []
        selections = {}
        for artifact in plan.artifacts:
            requirements = plan.requirements[artifact.id]
            if any(plan.runs[key].selected is None
                   for keys in requirements.values() for key in keys):
                continue
            ready.append(artifact)
            selections[artifact.id] = {}
            for name, keys in requirements.items():
                ids = list(dict.fromkeys(plan.runs[key].selected["run_id"] for key in keys))
                selections[artifact.id][name] = ids
                store.data["eval_requirements"].append(dict(invocation_id=invocation_id,
                    artifact_id=artifact.id, requirement=name))
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
            names = {row["requirement"] for row in store.data["eval_requirements"]
                     if row["invocation_id"] == saved and row["artifact_id"] == artifact.id}
            if names != set(artifact.requires):
                raise ValueError(f"saved invocation is missing requirements for {artifact.id}")
            selections[artifact.id] = {name: [row["run_id"] for row in sorted(rows, key=lambda r: r["position"])
                                            if row["requirement"] == name] for name in artifact.requires}
        invocation = self._invocation(store, options, argv)
        invocation["source_invocation"] = saved
        for artifact_id, scopes in selections.items():
            for name, ids in scopes.items():
                store.data["eval_requirements"].append(dict(invocation_id=invocation["invocation_id"],
                    artifact_id=artifact_id, requirement=name))
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
