"""Generate paper artifacts and a portable, embedded-data HTML report."""
from __future__ import annotations

import html
import inspect
import io
import json
from pathlib import Path
import re
from typing import Sequence

from .eval_live import Registry, _Output
from . import latex
from .store import atomic_write, scalar

PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.js"


def source_for(artifacts: Sequence[_Output], source: str | Path | None) -> str:
    """Find single-script callback source, or use explicitly supplied source."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    if isinstance(source, str):
        return source
    paths = {inspect.getsourcefile(fn) for artifact in artifacts
             for fn in (artifact.render, artifact.filter_source) if fn is not None}
    if None in paths or len(paths) != 1:
        raise ValueError("HTML callbacks need one source file; supply browser_source explicitly otherwise")
    return Path(paths.pop()).read_text(encoding="utf-8")


def write_html(path: str | Path, data: dict, artifacts: Sequence[_Output], selections: dict,
               *, source: str | Path | None = None, title: str = "Paper evaluation") -> Path:
    """Embed data and Python callbacks using the existing interactive viewer.

    Source is imported as a module, so a normal ``if __name__ == '__main__'``
    guard prevents collection in the browser. Keep local execution imports in
    that guarded entry point. Graphs/tables use the pinned Pyodide CDN; raw
    tables need no network. Top-level render/filter functions are required.
    """
    from . import css, js, pyodide_lib

    script = source_for(artifacts, source)
    descriptors = []
    for artifact in artifacts:
        for function in (artifact.render, artifact.filter_source):
            if function and (function.__name__ != function.__qualname__ or function.__name__ == "<lambda>"):
                raise ValueError("HTML callbacks must be named top-level functions")
        descriptors.append({"id": artifact.id, "kind": artifact.kind,
                            "title": artifact.title, "caption": artifact.caption,
                            "render": artifact.render.__name__,
                            "filter": artifact.filter_source.__name__ if artifact.filter_source else None})
    bootstrap = f'''import eval_live
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
    """Shared file renderer for Registry.run and Registry.render_to_dir."""
    output = Path(output)
    written = []
    macro_names = set()
    for artifact in outputs:
        value = artifact.render(data)
        if artifact.kind == "graph":
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
    return written


def render_artifacts(data: dict, artifacts: Sequence[_Output], selections: dict,
                     output: Path, *, source: str | Path | None, title: str) -> list[Path]:
    """Render selected registry outputs using the same callbacks in every format."""
    registry = Registry()
    registry._outputs = list(artifacts)
    bound = registry._bind(selections)
    written = render_outputs(data, bound._outputs, output)
    if artifacts:
        bound.render_to_console(data)
        written.append(write_html(output / "index.html", data, artifacts, selections,
                                  source=source, title=title))
    return written
