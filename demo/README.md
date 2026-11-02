# A complete paper evaluation

From the repository root:

```bash
pip install -e '.[render]'
python demo/eval.py
```

This single script uses one `Registry` for its benchmark, graphs, table, and
macros. It creates a tiny local Git repository with two commits and runs all
four paper outputs. It compares list/set membership across input
sizes and compares the same set configuration before and after an optimization.
No clone or compiler is needed. The full run requires 27 observations, shared
across the artifact declarations.

| File in `demo/out/paper/` | Contents |
| --- | --- |
| `results.json` | All flat measurement tables, CLI arguments, commit/configuration provenance, process diagnostics, and exact artifact sample selections |
| `runtime.pdf` | Current-commit list and set timings |
| `history.pdf` | The same configuration at two different commits |
| `summary.tex` | Median timings as a LaTeX table |
| `macros.tex` | `\EvalSamples`, `\EvalInputSizes`, and `\EvalBestMedianMs` |
| `index.html` | Interactive raw data, computed outputs, and graphs |

Run it again to reuse all observations. Select outputs or change measurement
parameters with CLI arguments:

```bash
python demo/eval.py
python demo/eval.py --dry-run
python demo/eval.py --samples 5
python demo/eval.py --only summary --sizes 500 2000
python demo/eval.py --seed 42
python demo/eval.py --timeout 60
python demo/eval.py --force
python demo/eval.py --render-only
python demo/eval.py --help
```

`--render-only` uses the latest successful invocation's exact selections. If
that invocation only contained a subset, pass the same `--only` selection or
use `--invocation ID` to choose a complete run. No benchmark repository is
needed for render-only use. Use `--data PATH` to share a cache across artifact
output directories. Every option and its default is recorded, even options
that do not affect measurement identity.

The demo defines `--samples` itself and includes a `sample` index in each
treatment. Both the total CLI count and the individual index affect cache
identity. Changing the count collects fresh results for that configuration;
repeating it reuses them. Median calculation is ordinary Python in the table
callback. `requires` functions select from the declared cross product; they
skip unused baseline/list combinations. An output without a selector would
request the whole catalog.

`--timeout` overrides the benchmark's per-command deadline in seconds. If a
command times out, raise the limit and run again: completed samples are reused
and only the missing samples are collected. Repeating an equal or smaller
limit leaves cached timeouts blocked. Use `--force` to retry anyway, including
ordinary failures. Attempts keep their original deadlines and diagnostics.

Open the HTML directly. Filter Summary by `algorithm = 'set' AND size = 500`
to narrow observations and recompute graphs. Raw tables work without network
access; interactive Python outputs fetch Pyodide and matplotlib from a CDN.
The page does not collect measurements.

Read [eval.py](eval.py) from the top: pure rendering callbacks, a benchmark
callback, fixture setup, and the registry declarations in `main`. Callbacks
receive ordinary data dictionaries; `select(data, name)` picks a named
requirement for the commit comparison. Git
worktrees, caching, sample selection, JSON persistence, LaTeX, and HTML assembly
are handled by eval-live. The fixture is in `demo/out/repository`; `--commit`
and `--baseline` select its revisions. `WORKTREE` explicitly selects current
files, and dirty collection requires `--allow-dirty` and bypasses reuse.

Timings are illustrative and vary with machine load. Container construction is
outside the timed region. Generated files are replaced individually; use a
separate output directory for subsets to avoid confusing them with older
paper files. The sibling `.lock` file is writer coordination, not stored data.

The [Python guide](../docs/python-guide.rst) covers callback contracts and the
[API reference](../docs/api.rst) is generated from docstrings. A second example,
[egglog_eval.py](egglog_eval.py), adapts a real egglog workload when a compatible
local repository and built binary are available.
