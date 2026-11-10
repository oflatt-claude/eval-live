"""Generate paper artifacts and a portable, embedded-data HTML report."""
from __future__ import annotations

from contextlib import contextmanager
import html
import inspect
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Sequence

from .eval_live import Registry, _Output
from . import latex
from .store import atomic_write, parameters, scalar, validate_tables

PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js"
REPORT_DATA = "results.json"
REPORT = "reproduce.json"


def discard_outputs(output: Path, artifacts: Sequence[_Output], *, fmt="pdf", table_json=False):
    """Remove expected artifacts, HTML, and files recorded by previous rendering.

    Generated filenames are reserved for eval-live. Files at other paths,
    including the measurement database, are preserved. Missing files are allowed.
    """
    names = {f"{item.id}.{fmt if item.kind == 'graph' else 'tex'}" for item in artifacts}
    if table_json:
        names.update(f"{item.id}.json" for item in artifacts if item.kind == "table")
    names.update(("index.html", REPORT))
    if (output / REPORT).exists():
        names.add(REPORT_DATA)
    manifest = output / ".eval-live-outputs"
    if manifest.exists():
        names.update(manifest.read_text().splitlines())
    for name in names:
        if name and Path(name).name == name and name not in (".", ".."):
            (output / name).unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)


@contextmanager
def output_directory(output: Path, artifacts: Sequence[_Output], *, fmt="pdf", table_json=False):
    """Yield an empty staging directory and publish its files on successful exit.

    Write only direct child files there. Previous generated outputs are removed
    on entry; exceptions, including Ctrl+C, remove partially published outputs
    and propagate. The caller must prevent concurrent rendering to ``output``.
    """
    output.mkdir(parents=True, exist_ok=True)
    discard_outputs(output, artifacts, fmt=fmt, table_json=table_json)
    with tempfile.TemporaryDirectory(prefix=".eval-live-", dir=output) as temporary:
        staging = Path(temporary)
        try:
            yield staging
            names = sorted(path.name for path in staging.iterdir())
            # Record the whole set before moving files so cleanup also covers a
            # failure partway through publication.
            atomic_write(output / ".eval-live-outputs", "\n".join(names) + "\n")
            for name in names:
                (staging / name).replace(output / name)
        except BaseException:
            discard_outputs(output, artifacts, fmt=fmt, table_json=table_json)
            raise


def browser_numbers(value):
    """Raise ValueError for whole numbers outside ±(2**53 - 1), without coercion.

    Checks nested dictionaries and sequences; string identifiers remain strings.
    The measurement store is independent of this browser export restriction.
    """
    if (type(value) is int or type(value) is float and value.is_integer()) and abs(value) > 2**53 - 1:
        raise ValueError(f"integer {value} exceeds the browser's exact range; use a string for identifiers")
    if isinstance(value, dict):
        for item in value.values():
            browser_numbers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            browser_numbers(item)


def source_for(artifacts: Sequence[_Output], source: str | Path | None) -> str:
    """Find single-script callback source, or use explicitly supplied source."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if isinstance(source, str):
        return source
    paths = {inspect.getsourcefile(fn) for artifact in artifacts
             for fn in (artifact.render, artifact.interactive_filter) if fn is not None}
    if None in paths or len(paths) != 1:
        raise ValueError("HTML callbacks need one source file; supply browser_source explicitly otherwise")
    return Path(paths.pop()).read_text(encoding="utf-8")


def browser_callbacks(artifacts: Sequence[_Output], source: str | Path | None) -> tuple[str, list[dict]]:
    """Validate selected browser callbacks and return their source and descriptors.

    Render and interactive filter callbacks must be named top-level functions.
    Without explicit source, all callbacks must come from one readable file.
    This checks export structure, not execution or browser package availability.
    """
    descriptors = []
    for artifact in artifacts:
        for function in (artifact.render, artifact.interactive_filter):
            if function and (function.__name__ != function.__qualname__ or function.__name__ == "<lambda>"):
                raise ValueError("HTML callbacks must be named top-level functions")
        descriptors.append({"id": artifact.id, "kind": artifact.kind,
                            "title": artifact.title, "caption": artifact.caption,
                            "render": artifact.render.__name__,
                            "filter": artifact.interactive_filter.__name__ if artifact.interactive_filter else None})
    return source_for(artifacts, source), descriptors


def report_data(data: dict, selections: dict) -> dict:
    """Export all cached attempts for the repository/commit pairs used by outputs.

    Preserve every configuration, version, outcome, and collecting invocation
    for those commits, including attempts not selected by any output. No extra
    measurements are collected. Empty tables retain their names.
    """
    selected = {run_id for scopes in selections.values() for runs in scopes.values() for run_id in runs}
    commits = {(row["repository"], row["commit"]) for row in data["eval_runs"] if row["run_id"] in selected}
    runs = [row for row in data["eval_runs"] if (row["repository"], row["commit"]) in commits]
    ids = {row["run_id"] for row in runs}
    configs = {row["config_id"] for row in runs}
    environments = {row["environment_id"] for row in runs}
    invocations = {row["invocation_id"] for row in runs}

    def keep(name, row):
        if "run_id" in row:
            return row["run_id"] in ids
        if "config_id" in row:
            return row["config_id"] in configs
        if "environment_id" in row:
            return row["environment_id"] in environments
        if "invocation_id" in row:
            return row["invocation_id"] in invocations
        return name == "eval_metadata"

    return {name: [dict(row) for row in rows if keep(name, row)] for name, rows in data.items()}


def read_report(path: Path, data: dict) -> dict:
    """Read a generation record; reject malformed or unavailable run selections."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if type(report["schema_version"]) is not int or report["schema_version"] != 1:
            raise ValueError("unsupported report schema")
        if report["data"] != REPORT_DATA:
            raise ValueError(f"report data must be {REPORT_DATA}")
        parameters(report["arguments"])
        validate_tables({"invocation": [report["invocation"]]})
        if not isinstance(report["invocation"]["invocation_id"], str):
            raise ValueError("missing invocation identity")
        if not isinstance(report["argv"], list) or any(not isinstance(token, str) for token in report["argv"]):
            raise ValueError("invalid report argv")
        runs = {row["run_id"] for row in data["eval_runs"]}
        for artifact, scopes in report["selections"].items():
            if not isinstance(artifact, str) or not artifact or not isinstance(scopes, dict):
                raise ValueError("invalid output selection")
            for name, ids in scopes.items():
                if (not isinstance(name, str) or not name or not isinstance(ids, list)
                        or any(not isinstance(run, str) or run not in runs for run in ids)
                        or len(set(ids)) != len(ids)):
                    raise ValueError("invalid or unavailable selected runs")
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(f"invalid or unavailable report in {path}: {error}") from error


def write_html(path: str | Path, data: dict, artifacts: Sequence[_Output], selections: dict,
               *, source: str | Path | None = None, title: str = "Paper evaluation") -> Path:
    """Write an HTML report containing data, saved selections, and Python callbacks.

    Source is imported as a module, so a normal ``if __name__ == '__main__'``
    guard prevents collection in the browser. Keep local execution imports in
    that guarded entry point. Graphs/tables use the pinned Pyodide CDN; raw
    tables need no network. Top-level render/filter functions are required.
    Whole numbers outside JavaScript's exact range (plus or minus ``2**53 - 1``)
    raise ``ValueError``. Encode large identifiers as strings.
    """
    from . import css, js, pyodide_lib

    script, descriptors = browser_callbacks(artifacts, source)
    bootstrap = f'''import eval_live
import sys
sys.modules.pop("_eval_live_report", None)
import _eval_live_report as report
_reg = eval_live.Registry()
for _item in {descriptors!r}:
    _reg._add(_item["kind"], _item["title"], getattr(report, _item["render"]),
              None, _item["id"], _item["caption"],
              getattr(report, _item["filter"]) if _item["filter"] else None)
_reg._selections = {selections!r}
eval_live.registry = _reg
'''
    modules = {"_eval_live_report.py": script}
    library = pyodide_lib()

    def literal(value):
        browser_numbers(value)
        return json.dumps(value, allow_nan=False).replace("<", "\\u003c")

    library_js = re.sub(r"</script", r"<\\/script", js(), flags=re.I)
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css()} body {{ font-family: system-ui, sans-serif; margin: 2rem; }}</style>
</head><body>
<p>Explore the saved evaluation with table filters. Interactive Python outputs load a runtime from the Pyodide CDN.</p>
<div id="report"></div>
<script src="{PYODIDE_URL}"></script>
<script>{library_js}</script>
<script>initEvalLive("report", {literal(data)}, {literal(title)},
{literal(bootstrap)}, {literal(library)}, {literal(modules)});</script>
</body></html>
'''
    destination = Path(path)
    atomic_write(destination, page)
    return destination


def render_outputs(data: dict, registry: Registry, output: str | Path, *,
                   fmt: str = "pdf", dpi: int = 150, table_json: bool = False) -> list[Path]:
    """Replace generated files using supplied data and return their paths.

    Evaluate each output once. ``None`` omits a graph. With ``table_json=True``,
    nonempty tables produce JSON and LaTeX; otherwise even empty tables produce
    a LaTeX file. Exceptions leave no generated report. No measurements are run.
    """
    output = Path(output)
    with output_directory(output, registry._outputs, fmt=fmt, table_json=table_json) as staging:
        written, _ = _render_outputs(data, registry, staging, fmt=fmt, dpi=dpi, table_json=table_json,
                                     capture_errors=False)
    return [output / path.name for path in written]


def _render_outputs(data, registry, output, *, fmt="pdf", dpi=150, table_json=False, capture_errors=True):
    """Write to a staging directory and return paths plus console table entries.

    Console entries hold the values already exported to files, so printing them
    does not invoke the paper's callbacks again. This function does not publish.
    """
    written = []
    tables = []
    macro_names = set()
    for artifact in registry._outputs:
        value = registry._evaluate(artifact, data, capture_errors=capture_errors)
        if artifact.kind == "graph":
            if value is None:
                continue
            import matplotlib.pyplot as plt
            buffer = io.BytesIO()
            try:
                value.savefig(buffer, format=fmt, dpi=dpi, bbox_inches="tight")
            finally:
                plt.close(value)
            path = output / f"{artifact.id}.{fmt}"
            atomic_write(path, buffer.getvalue())
        elif artifact.kind == "table":
            tables.append(artifact.table_result(value))
            if table_json:
                if not value:
                    continue
                json_path = output / f"{artifact.id}.json"
                atomic_write(json_path, json.dumps(value, indent=2))
                written.append(json_path)
            columns = list(dict.fromkeys(key for row in value for key in row))
            path = output / f"{artifact.id}.tex"
            atomic_write(path, latex.table(value, columns) if columns else "% No matching rows.\n")
        elif artifact.kind == "macros":
            tables.append(artifact.table_result(value))
            lines = []
            for name, item in value.items():
                if not re.fullmatch(r"[A-Za-z]+", name) or name in macro_names:
                    raise ValueError(f"invalid or duplicate LaTeX macro name: {name!r}")
                macro_names.add(name)
                lines.append(f"\\newcommand{{\\{name}}}{{{latex.escape(scalar(item))}}}\n")
            path = output / f"{artifact.id}.tex"
            atomic_write(path, "".join(lines))
        else:
            raise ValueError(f"unsupported output: {artifact.kind}")
        written.append(path)
    return written, tables


def render_artifacts(data: dict, artifacts: Sequence[_Output], selections: dict,
                     output: Path, *, source: str | Path | None, title: str, report: dict) -> list[Path]:
    """Publish paper files, HTML, cache-format data, and their generation record.

    Evaluate tables and macros once for files and terminal output. Rendered
    outputs use exact selections; raw HTML and exported data include every
    cached attempt from the selected commits. Reported errors prevent
    publication. Diagnostics are deduplicated and summarized by level.
    """
    registry = Registry()
    registry._outputs = list(artifacts)
    printed = set()

    def message(level, text):
        if (level, text) not in printed:
            printed.add((level, text))
            print(f"{level.upper()}: {text}", file=sys.stderr)

    registry._message_handler = message
    registry._selections = selections
    if report["invocation"]["script_dirty"]:
        warning = ("The evaluation script's checkout has uncommitted changes; "
                   "the recorded script commit cannot reproduce those changes. Commit before generating a final report.")
        registry._messages[None] = {("warning", warning): None}
        message("warning", warning)
    try:
        with output_directory(output, artifacts) as staging:
            written, tables = _render_outputs(data, registry, staging)
            if artifacts:
                from . import console
                console.render(tables)
                written.append(write_html(staging / "index.html", data, artifacts, selections,
                                          source=source, title=title))
            if registry.run_messages()[1]["rows"]:
                raise RuntimeError("rendering reported errors")
            snapshot = staging / REPORT_DATA
            atomic_write(snapshot, json.dumps(data, indent=2, allow_nan=False) + "\n")
            written.append(snapshot)
            record = staging / REPORT
            atomic_write(record, json.dumps(report, indent=2, allow_nan=False) + "\n")
            written.append(record)
    finally:
        for table in registry.run_messages():
            if table["rows"]:
                print(f"{table['name']}:", file=sys.stderr)
                for row in table["rows"]:
                    print(f"  {row['message']}", file=sys.stderr)
    return [output / path.name for path in written]
