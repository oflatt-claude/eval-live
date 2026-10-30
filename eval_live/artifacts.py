"""Browser-compatible artifact definitions and views of selected observations."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class RunRequest:
    """Data required from one benchmark, commit, and configuration.

    ``repository`` is a local Git repository; ``commit`` is a ref or full SHA.
    Use ``WORKTREE`` explicitly to execute current working files. Dirty
    worktrees require ``--allow-dirty`` and never reuse persisted observations.
    ``config`` contains scalar or scalar-list parameters. ``samples`` is the
    minimum observation count and does not affect an observation's cache key.
    """

    benchmark: str
    config: Mapping[str, Any] = field(default_factory=dict)
    repository: str = "."
    commit: str = "HEAD"
    samples: int = 1

    def __post_init__(self):
        if type(self.samples) is not int or self.samples < 1:
            raise ValueError("samples must be a positive integer")
        if not self.benchmark or not self.commit:
            raise ValueError("benchmark and commit must be nonempty")
        frozen = {}
        for name, value in self.config.items():
            if not isinstance(name, str):
                raise ValueError("configuration names must be strings")
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                if item is not None and type(item) not in (str, int, bool, float):
                    raise ValueError("configurations contain only scalars and scalar lists")
                if type(item) is float and not math.isfinite(item):
                    raise ValueError("configuration numbers must be finite")
            frozen[name] = tuple(values) if isinstance(value, (list, tuple)) else value
        object.__setattr__(self, "config", MappingProxyType(frozen))


@dataclass(frozen=True)
class Artifact:
    """An output with named data requirements and a pure render callback.

    Each requirement is a ``RunRequest`` or a sequence of requests. ``render``
    receives an ``ArtifactView``. The optional ``filter_source`` callback takes
    visible computed rows and that view, and returns matching ``run_id`` values.
    Render callbacks used in HTML must be top-level functions in browser source.
    """

    id: str
    requires: Mapping[str, RunRequest | Sequence[RunRequest]]
    render: Callable
    title: str | None = None
    caption: str | None = None
    filter_source: Callable | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", self.id):
            raise ValueError("artifact IDs must be filename-safe letters, digits, underscores, or hyphens")
        if not callable(self.render):
            raise ValueError("artifact render must be callable")
        requirements = {}
        for name, requests in self.requires.items():
            if not isinstance(name, str) or not name:
                raise ValueError("requirement names must be nonempty strings")
            requests = (requests,) if isinstance(requests, RunRequest) else tuple(requests)
            if not requests or any(not isinstance(request, RunRequest) for request in requests):
                raise ValueError("each requirement must contain at least one RunRequest")
            requirements[name] = requests
        if not requirements:
            raise ValueError("an artifact must declare at least one data requirement")
        object.__setattr__(self, "requires", MappingProxyType(requirements))


class Figure(Artifact):
    """A figure callback returning a matplotlib Figure."""


class Table(Artifact):
    """A table callback returning a list of row dictionaries."""


class Macros(Artifact):
    """A callback returning ``{LaTeXMacroName: scalar_value}``. Values are escaped."""


class ArtifactView:
    """Read-only selection of named data requirements over a data snapshot.

    ``view['baseline']`` returns named tables scoped to baseline sample IDs.
    ``view.data`` is shorthand when there is exactly one named requirement.
    Row dictionaries are copied so callbacks cannot mutate stored data.
    """

    def __init__(self, data: dict, selections: Mapping[str, Sequence[str]]):
        self._data = data
        self.selections = {name: tuple(ids) for name, ids in selections.items()}

    def __getitem__(self, name: str) -> dict:
        ids = set(self.selections[name])
        result = {
            table: [dict(row) for row in rows if row.get("run_id") in ids]
            for table, rows in self._data.items()
            if not table.startswith("eval_") or table == "eval_runs"
        }
        configs = {row["config_id"] for row in result.get("eval_runs", [])}
        result["eval_configs"] = [dict(row) for row in self._data.get("eval_configs", [])
                                   if row["config_id"] in configs]
        return result

    @property
    def data(self) -> dict:
        """Return the sole requirement's data, or raise for ambiguous views."""
        if len(self.selections) != 1:
            raise ValueError("use view[requirement_name] for an artifact with multiple requirements")
        return self[next(iter(self.selections))]


def registry_for(artifacts: Sequence[Artifact], selections: dict):
    """Adapt artifacts to the existing Registry, locally or in Pyodide."""
    from eval_live import Registry

    registry = Registry()
    for artifact in artifacts:
        scope = selections[artifact.id]

        def render(data, artifact=artifact, scope=scope):
            return artifact.render(ArtifactView(data, scope))

        def narrow(rows, data, artifact=artifact, scope=scope):
            ids = set(artifact.filter_source(rows, ArtifactView(data, scope)))
            allowed = {run_id for values in scope.values() for run_id in values}
            if not ids <= allowed:
                raise ValueError("filter_source returned run IDs outside its requirements")
            return {name: [row for row in values if "run_id" not in row or row["run_id"] in ids]
                    if not name.startswith("eval_") or name == "eval_runs" else values
                    for name, values in data.items()}

        name = artifact.title or artifact.id
        if isinstance(artifact, Figure):
            registry.graph(name, render)
        elif isinstance(artifact, Table):
            registry.table(name, render, filter_source=narrow if artifact.filter_source else None,
                           caption=artifact.caption)
        elif isinstance(artifact, Macros):
            def macro_table(data, render=render):
                return [{"macro": name, "value": value} for name, value in render(data).items()]
            registry.table(name, macro_table, caption=artifact.caption)
    return registry
