"""eval_live – lightweight library for registering visualizations.

Create a Registry, add graphs and tables to it, and assign it to the
module-level ``registry`` variable so eval-live can find it.

Quick start::

    import eval_live

    reg = eval_live.Registry()
    reg.graph("My Graph", my_graph)
    reg.table("Mean Timing", mean_timing, filter_source=mean_timing_filter)
    eval_live.registry = reg

Graphs
------
A graph function receives the data dict and returns a matplotlib Figure::

    def my_graph(data):
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.bar(["a", "b"], [1, 2])
        return fig

Tables
------
A table function receives the data dict and returns a list of row dicts.
These are rendered as filterable HTML tables below the raw data tables::

    def mean_timing(data):
        result = []
        for row in data["timings"]:
            times = row["timing_list"]
            n = len(times)
            mean = sum(times) / n if n else 0
            result.append({"file": row["file"], "mean": round(mean, 4), "n": n})
        return result

Filters (filter_source)
-----------------------
When the user types in a computed table's filter box, only some computed
rows remain visible.  A ``filter_source`` function lets you propagate that
filter back to the *raw* data tables.

It receives two arguments:

- **filtered_rows**: the list of computed row dicts that are currently
  visible after the user's text filter.
- **data**: the *original* (unfiltered) data dict.

It returns a **new data dict** with the appropriate raw rows removed.
eval-live then hides raw-table rows that aren't in the returned data.

Example: if your computed table has one row per (file, treatment) pair,
the filter_source narrows raw timings to matching pairs::

    def mean_timing_filter(filtered_rows, data):
        # Build the set of primary keys the user wants to see
        keys = {(r["file"], r["treatment"]) for r in filtered_rows}
        # Return data with only the matching raw rows
        return {
            **data,
            "timings": [r for r in data["timings"]
                        if (r["file"], r["treatment"]) in keys],
        }

If multiple computed tables each have a filter_source, they are chained:
each one narrows the data further.  A table without filter_source has no
effect on the raw tables when filtered.
"""
from __future__ import annotations

import io
import base64
from dataclasses import dataclass
import re
import sys
from types import MappingProxyType
from typing import Callable, Mapping

# The user script sets this module-level variable.
registry = None
_message_sink = None


def warn(message):
    """Report a computed warning without failing the evaluation.

    Identical messages are deduplicated across outputs and never stored.
    """
    _report("warning", message)


def error(message):
    """Report a computed error and continue rendering the remaining outputs.

    ``Registry.run`` fails after rendering if any errors were reported.
    Identical messages are deduplicated across outputs and never stored.
    """
    _report("error", message)


def _report(level, message):
    message = str(message)
    if _message_sink is None:
        print(f"{level.upper()}: {message}", file=sys.stderr)
    else:
        _message_sink(level, message)


@dataclass(frozen=True)
class _Output:
    """One registry entry; shared by planning, local export, and the browser."""

    kind: str
    id: str
    title: str
    render: Callable
    requires: Mapping
    caption: str | None = None
    filter_source: Callable | None = None


def _requirements(requires):
    if requires is None or requires == {}:
        return {"data": None}
    if callable(requires):
        requires = {"data": requires}
    if not isinstance(requires, Mapping):
        raise ValueError("requires must be a treatment selector or named selectors")
    if any(not isinstance(name, str) or not name or not callable(selector)
           for name, selector in requires.items()):
        raise ValueError("each named requirement must be a treatment selector")
    return MappingProxyType(dict(requires))


def _select_rows(data, ids):
    result = {
        table: [dict(row) for row in rows if row.get("run_id") in ids]
        for table, rows in data.items()
        if not table.startswith("eval_") or table in ("eval_runs", "eval_selections")
    }
    configs = {row["config_id"] for row in result.get("eval_runs", [])}
    result["eval_configs"] = [dict(row) for row in data.get("eval_configs", [])
                              if row["config_id"] in configs]
    result["eval_requirements"] = [dict(row) for row in data.get("eval_requirements", [])]
    return result


def _scoped_data(data, selections):
    result = _select_rows(data, {run_id for ids in selections.values() for run_id in ids})
    # A callback sees only this output's named selections, even when the same
    # observation has different roles in other outputs or earlier invocations.
    result["eval_requirements"] = [{"requirement": name} for name in selections]
    result["eval_selections"] = [{"requirement": name, "run_id": run_id}
                                for name, ids in selections.items() for run_id in ids]
    return result


def select(data: dict, requirement: str) -> dict:
    """Select one named requirement from a registry callback's data dictionary.

    For ``requires={"baseline": old, "candidate": new}``, use
    ``select(data, "baseline")["measurements"]``. The callback's ordinary data
    tables contain the union of its requirements. Returned rows are copied,
    and selection still works after interactive filters remove all measurements.
    """
    rows = data.get("eval_selections", [])
    names = {row["requirement"] for row in data.get("eval_requirements", []) + rows}
    if requirement not in names:
        raise ValueError(f"unknown requirement: {requirement!r}")
    return _select_rows(data, {row["run_id"] for row in rows if row["requirement"] == requirement})


class Registry:
    """Register a paper's benchmarks, graphs, tables, and LaTeX macros.

    Rendering callbacks receive dictionaries of named tables. ``requires=``
    selects treatments for each output. ``run(args=..., argv=...)`` collects
    their data and generates paper outputs plus interactive HTML.

    ``selection_arguments`` names paper-specific options that select work
    without changing measurements. ``browser_source`` is a Path or source
    string containing top-level rendering callbacks; their common source file
    is used by default. Guard local execution with ``if __name__ == '__main__'``.
    """

    def __init__(self, *, selection_arguments=(), browser_source=None, title="Paper evaluation"):
        self._outputs = []
        self._benchmarks = {}
        self.selection_arguments = set(selection_arguments)
        self.browser_source = browser_source
        self.title = title
        self._messages = {}
        self._message_handler = None

    def _invoke(self, owner, function, *args, default=None):
        global _message_sink
        identity = (id(self._messages), owner)
        if getattr(_message_sink, "owner", None) == identity:
            return function(*args)
        messages = {}

        def emit(level, message):
            messages[level, message] = None
            if self._message_handler:
                self._message_handler(level, message)

        emit.owner = identity
        previous, _message_sink = _message_sink, emit
        try:
            return function(*args)
        except Exception as exception:
            emit("error", f"{owner}: {exception}")
            return default
        finally:
            self._messages[owner] = messages
            _message_sink = previous

    def run_messages(self):
        """Return derived Warnings and Errors tables from the latest callbacks.

        Re-evaluating a callback replaces its previous messages. Identical
        level/message pairs are deduplicated across outputs. Nothing is added
        to the measurement data or its cache.
        """
        messages = dict.fromkeys(item for group in self._messages.values() for item in group)
        return [{"name": name, "rows": [{"message": message} for kind, message in messages if kind == level],
                 "has_filter_source": False, "caption": None}
                for level, name in (("warning", "Warnings"), ("error", "Errors"))]

    def benchmark(self, name, fn=None, *, version, error_rows, treatments=None, repository=".", commit=None, **options):
        """Register ``fn(context) -> named flat tables`` for one observation.

        ``treatments`` is an iterable of configuration dictionaries (default:
        one empty configuration). Selectors see all registered treatments,
        with ``benchmark``, ``repository``, and ``commit`` fields added. A
        treatment can override repository/commit; otherwise use the registered
        values and the CLI's ``--commit`` (default HEAD).

        Required ``error_rows(treatment, status)`` returns named flat tables
        for ``pending``, ``timed-out``, ``failure``, and ``interrupted`` work.
        It must be pure and supply rows in the same tables for every status.
        All requested rows are saved before collection. Successful results
        replace placeholders and must cover every declared table. Timeouts and
        failures are ordinary data; renderers choose whether to warn or error.

        Treatments run sequentially unless marked ``parallel=True``. Marked
        treatments may use separate processes up to ``--jobs`` (CPU count by
        default); unmarked treatments run alone. Parallel collectors must be
        importable top-level functions. The flag and worker limit are saved
        with the configuration and affect cache identity.

        ``version`` identifies the tables' format/meaning and affects cache
        identity. Options are fields of :class:`eval_live.Benchmark`:
        ``inputs``, ``dependencies``, ``build``,
        ``build_config``, ``environment``, ``tools``, and ``timeout`` (seconds
        per command, default None for no limit). Registration does not collect.
        Omit ``fn`` to use ``@reg.benchmark(name, version=..., ...)``.
        """
        if fn is None:
            return lambda callback: self.benchmark(name, callback, version=version, error_rows=error_rows, treatments=treatments,
                                                   repository=repository, commit=commit, **options)
        if not callable(fn):
            raise ValueError("benchmark callback must be callable")
        if name in self._benchmarks:
            raise ValueError(f"duplicate benchmark: {name!r}")
        self._benchmarks[name] = {
            "benchmark": dict(name=name, run=fn, version=version, error_rows=error_rows, **options),
            "treatments": tuple(dict(row) for row in ([{}] if treatments is None else treatments)),
            "repository": str(repository), "commit": commit,
        }
        return fn

    def _add(self, kind, name, fn, requires, id, caption=None, filter_source=None):
        if fn is None:
            return lambda callback: self._add(kind, name, callback, requires, id, caption, filter_source)
        if not callable(fn):
            raise ValueError("output callback must be callable")
        output_id = id
        if output_id is None and kind == "table":
            output_id = getattr(fn, "__name__", None)
            if output_id == "<lambda>":
                output_id = None
        if output_id is None:
            output_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "graph"
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]*", output_id):
            raise ValueError("output IDs must be filename-safe letters, digits, underscores, or hyphens")
        self._outputs.append(_Output(kind, output_id, name, fn, _requirements(requires), caption, filter_source))
        return fn

    def graph(self, name, fn=None, *, requires=None, id=None):
        """Register ``fn(data) -> matplotlib.figure.Figure``.

        ``name`` is the display title. ``requires(treatments)`` returns the
        treatments needed by this output. Omit it to select the whole catalog.
        A mapping of named selector functions supports comparisons. Selectors
        receive read-only dictionaries and must return members of the catalog.
        ``run`` passes only this output's selected observations to the callback.
        ``id`` defaults to a slug of the name and controls filenames and
        ``--only`` selection. Direct ``run_graphs(data)`` uses the supplied data.
        Use ``@reg.graph(name, ...)`` to register a function where it is defined.
        Both forms return the original function, without wrapping it.
        """
        return self._add("graph", name, fn, requires, id)

    def table(self, name, fn=None, filter_source=None, *, caption=None, requires=None, id=None):
        """Register ``fn(data) -> list[dict]``; row keys are column headings.

        Cells can be scalars or styled ``{"text": ..., "style": ...}`` values.
        ``filter_source(filtered_rows, data) -> data`` returns a dictionary
        with raw tables narrowed to matching rows.
        Filters are chained in registration order. ``caption`` is shown below
        the table in the terminal and browser. ``requires`` works as in :meth:`graph`.
        ``id`` defaults to the function name and controls filenames and ``--only``.
        Lambdas and callables without names use a slug of the display title.
        Set ``id`` explicitly when registering the same function more than once.
        ``@reg.table(name, ...)`` is the equivalent decorator form.
        """
        return self._add("table", name, fn, requires, id, caption, filter_source)

    def macros(self, name, fn=None, *, requires=None, id=None):
        """Register ``fn(data) -> {LaTeXMacroName: scalar_value}``.

        Macro names contain letters only; values are LaTeX-escaped. Local
        export writes a ``.tex`` file, and the browser/terminal shows the names
        and values as a table. ``requires`` and ``id`` work as in :meth:`graph`.
        ``@reg.macros(name, ...)`` is the equivalent decorator form.
        """
        return self._add("macros", name, fn, requires, id)

    def plan(self, *, args=None):
        """Inspect selected outputs, cached observations, and missing work without writes."""
        from .planning import _Runner
        return _Runner(self).plan(args=args)

    def run(self, *, args=None, argv=None):
        """Collect required observations and write paper outputs plus HTML.

        Save all CLI arguments/defaults and data as flat tables in one JSON
        file. ``--timeout`` overrides command deadlines; raising it preserves
        successful samples and recollects timed-out work. ``--dry-run`` inspects
        the plan; ``--render-only`` replays saved selections without benchmarks.
        Every requested treatment has measured or explicit error rows. Ctrl+C
        stops collection and rendering. Figure warnings/errors are computed,
        deduplicated, and never stored; reporting errors fail after rendering.
        """
        from .planning import _Runner
        return _Runner(self).run(args=args, argv=argv)

    @property
    def _graphs(self):
        return [(output.title, output.render) for output in self._outputs if output.kind == "graph"]

    @property
    def _tables(self):
        result = []
        for output in self._outputs:
            if output.kind == "graph":
                continue
            fn = output.render
            if output.kind == "macros":
                def fn(data, render=fn):
                    return [{"macro": name, "value": value} for name, value in render(data).items()]
            result.append((output.title, fn, output.filter_source, output.caption))
        return result

    def _bind(self, selections):
        """Use the same registry callbacks with a saved selection per output."""
        bound = Registry()
        bound._messages = self._messages
        bound._message_handler = self._message_handler
        for output in self._outputs:
            scope = selections[output.id]

            def render(data, output=output, scope=scope):
                default = {} if output.kind == "macros" else [] if output.kind == "table" else None
                return self._invoke(output.title, output.render, _scoped_data(data, scope), default=default)

            def narrow(rows, data, output=output, scope=scope):
                narrowed = output.filter_source(rows, _scoped_data(data, scope))
                return {**data, **narrowed}

            bound._add(output.kind, output.title, render, None, output.id, output.caption,
                       narrow if output.filter_source else None)
        return bound

    def run_graphs(self, data):
        """Run all registered graph functions and return rendered results.

        Returns a list of ``{"name": str, "src": str}`` dicts where *src*
        is a base64 data-URI PNG.  Called by the JS engine.
        """
        import matplotlib
        matplotlib.use("AGG")
        import matplotlib.pyplot as plt

        results = []
        for name, fn in self._graphs:
            fig = self._invoke(name, fn, data)
            if fig is None:
                continue
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150)
            plt.close(fig)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode()
            results.append({"name": name, "src": f"data:image/png;base64,{b64}"})
        return results

    def render_to_dir(self, data, outdir, fmt="pdf", dpi=150):
        """Render supplied data to graph, table, and macro files locally.

        Graphs use ``fmt`` (PDF by default); nonempty tables produce JSON
        and LaTeX. Macros produce
        LaTeX. Return the written paths. No benchmarks run: ``requires`` is used
        by :meth:`run`, which stores data in one JSON file without table sidecars.
        """
        from .render import render_outputs
        return [str(path) for path in render_outputs(data, self._outputs, outdir,
                                                    fmt=fmt, dpi=dpi, table_json=True)]

    def run_tables(self, data):
        """Run all registered table functions and return their rows.

        Returns a list of ``{"name": str, "rows": list[dict],
        "has_filter_source": bool, "caption": str|None}`` dicts.  Called by the
        JS engine.
        """
        results = []
        for name, fn, fs, caption in self._tables:
            results.append({
                "name": name,
                "rows": self._invoke(name, fn, data, default=[]),
                "has_filter_source": fs is not None,
                "caption": caption,
            })
        return results

    def render_to_console(self, data, tables=None, console=None):
        """Render registered tables to a terminal with rich (graphs are skipped).

        ``tables`` is an optional ordered list of table names to render (others
        skipped; empty tables skipped); ``None`` renders all. ``console`` is an
        optional ``rich.Console``; it is returned. See ``console.py``.
        """
        from . import console as console_renderer

        return console_renderer.render(self._tables, data, console, names=tables)

    def apply_table_filters(self, table_filters, data):
        """Chain filter_source functions for all filtered computed tables.

        Called by the JS engine when the user filters one or more computed
        tables.

        Parameters
        ----------
        table_filters : list[dict]
            Each entry is ``{"name": str, "filtered_rows": list[dict]}``
            representing a computed table whose text filter is active.
        data : dict
            The original (unfiltered) data dict passed in by the JS
            engine.  Each ``filter_source`` is called in registration
            order, and each one receives the data already narrowed by
            the previous ``filter_source`` calls.

        Returns
        -------
        dict
            A new data dict with raw rows narrowed by each filter_source
            function in registration order.
        """
        filtered_data = data
        for name, _fn, fs, _caption in self._tables:
            if fs is None:
                continue
            for tf in table_filters:
                if tf["name"] == name:
                    filtered_data = fs(tf["filtered_rows"], filtered_data)
                    break
        return filtered_data
