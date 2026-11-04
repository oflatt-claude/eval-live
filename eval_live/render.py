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
from .store import atomic_write, scalar

PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js"


def discard_outputs(output: Path, artifacts: Sequence[_Output], *, fmt="pdf", table_json=False):
    """Remove expected artifacts, HTML, and files recorded by previous rendering.

    Generated filenames are reserved for eval-live. Files at other paths,
    including the measurement database, are preserved. Missing files are allowed.
    """
    names = {f"{item.id}.{fmt if item.kind == 'graph' else 'tex'}" for item in artifacts}
    if table_json:
        names.update(f"{item.id}.json" for item in artifacts if item.kind == "table")
    names.add("index.html")
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

    script = source_for(artifacts, source)
    descriptors = []
    for artifact in artifacts:
        for function in (artifact.render, artifact.interactive_filter):
            if function and (function.__name__ != function.__qualname__ or function.__name__ == "<lambda>"):
                raise ValueError("HTML callbacks must be named top-level functions")
        descriptors.append({"id": artifact.id, "kind": artifact.kind,
                            "title": artifact.title, "caption": artifact.caption,
                            "render": artifact.render.__name__,
                            "filter": artifact.interactive_filter.__name__ if artifact.interactive_filter else None})
    bootstrap = f'''import eval_live
import sys
sys.modules.pop("_eval_live_report", None)
import _eval_live_report as report
_reg = eval_live.Registry()
for _item in {descriptors!r}:
    _reg._add(_item["kind"], _item["title"], getattr(report, _item["render"]),
              None, _item["id"], _item["caption"],
              getattr(report, _item["filter"]) if _item["filter"] else None)
eval_live.registry = _reg._bind({selections!r})
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


def render_outputs(data: dict, outputs: Sequence[_Output], output: str | Path, *,
                   fmt: str = "pdf", dpi: int = 150, table_json: bool = False) -> list[Path]:
    """Replace generated files using supplied data and return their paths.

    Evaluate each output once. ``None`` omits a graph. With ``table_json=True``,
    nonempty tables produce JSON and LaTeX; otherwise even empty tables produce
    a LaTeX file. Exceptions leave no generated report. No measurements are run.
    """
    output = Path(output)
    with output_directory(output, outputs, fmt=fmt, table_json=table_json) as staging:
        written, _ = _render_outputs(data, outputs, staging, fmt=fmt, dpi=dpi, table_json=table_json)
    return [output / path.name for path in written]


def _render_outputs(data, outputs, output, *, fmt="pdf", dpi=150, table_json=False):
    """Write to a staging directory and return paths plus console table entries.

    Console entries hold the values already exported to files, so printing them
    does not invoke the paper's callbacks again. This function does not publish.
    """
    written = []
    tables = []
    macro_names = set()
    for artifact in outputs:
        value = artifact.render(data)
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
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError("table callbacks must return a list of row dictionaries")
            tables.append((artifact.title, lambda _data, rows=value: rows, None, artifact.caption))
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
            rows = [{"macro": name, "value": item} for name, item in value.items()]
            tables.append((artifact.title, lambda _data, rows=rows: rows, None, artifact.caption))
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
                     output: Path, *, source: str | Path | None, title: str) -> list[Path]:
    """Render saved per-output selections to paper files, the terminal, and HTML.

    Tables and macros are evaluated once for files and terminal output. The HTML
    contains callbacks for browser execution. Reported errors raise RuntimeError
    before publishing files; warnings allow publication. Diagnostics are printed
    as they occur and summarized by level, with duplicate messages removed.
    """
    registry = Registry()
    registry._outputs = list(artifacts)
    printed = set()

    def message(level, text):
        if (level, text) not in printed:
            printed.add((level, text))
            print(f"{level.upper()}: {text}", file=sys.stderr)

    registry._message_handler = message
    bound = registry._bind(selections)
    try:
        with output_directory(output, artifacts) as staging:
            written, tables = _render_outputs(data, bound._outputs, staging)
            if artifacts:
                from . import console
                console.render(tables, data)
                written.append(write_html(staging / "index.html", data, artifacts, selections,
                                          source=source, title=title))
            if registry.run_messages()[1]["rows"]:
                raise RuntimeError("rendering reported errors")
    finally:
        for table in registry.run_messages():
            if table["rows"]:
                print(f"{table['name']}:", file=sys.stderr)
                for row in table["rows"]:
                    print(f"  {row['message']}", file=sys.stderr)
    return [output / path.name for path in written]
