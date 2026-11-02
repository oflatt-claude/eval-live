Writing a paper evaluation
==========================

Use one ``Registry`` for the collection callback and the paper's graphs,
tables, and macros. ``demo/eval.py`` is a complete single-script example:

.. code-block:: bash

   pip install -e '.[render,docs]'
   python demo/eval.py
   python demo/eval.py                         # reuse the saved results
   python demo/eval.py --dry-run               # inspect selected treatments
   python demo/eval.py --only summary --sizes 500
   python demo/eval.py --samples 5             # a new CLI configuration
   python demo/eval.py --timeout 60            # retry eligible timeouts
   python demo/eval.py --render-only           # regenerate from saved selections

The demo creates a tiny local Git repository with two commits. No clone or
compiler is needed. It writes one ``demo/out/paper/results.json`` data/cache
file plus ``runtime.pdf``, ``history.pdf``, ``summary.tex``, ``macros.tex``, and
``index.html``. The sibling lock file is writer coordination, not stored data.
A first-ever dry-run requires initializing the fixture by running the demo.

Declare treatments and a collection callback
--------------------------------------------

A treatment is an ordinary configuration dictionary. Declare the finite set
you know how to run, using a list, comprehension, or ``itertools.product``.
Each selected treatment invokes the registered callback once:

.. code-block:: python

   def collect(context):
       import json
       import sys
       result = context.command([
           sys.executable, "worker.py", context.config["suite"],
           str(context.config["size"]),
       ])
       return {"measurements": [json.loads(result.stdout)]}

   reg = eval_live.Registry()
   reg.benchmark(
       "worker", collect, version="1", inputs=["worker.py"],
       treatments=[{"suite": suite, "size": size}
                   for suite in ("a", "b") for size in (100, 1000)],
   )

The callback returns named flat tables. Cells contain scalars; repeated values
belong in separate rows. ``eval_`` table names and the ``run_id`` column are
reserved. The library adds ``run_id`` to link every returned row to the full
treatment, CLI configuration, commit, data version, and diagnostics.

``context.config`` holds the treatment fields. ``context.directory`` contains
the requested Git revision in a temporary detached worktree. Commands run
there by default, without a shell, and record argv, duration, exit status,
stdout, and stderr. Nonzero exits and timeouts raise ``ProcessError``.
``run_command`` also exposes this subprocess helper separately.

``repository`` defaults to the current directory. ``commit`` defaults to the
CLI's ``--commit`` (HEAD by default). Treatments can override either field.
Selectors see those fields and ``benchmark`` alongside the treatment's own
parameters; these three reserved fields are omitted from ``context.config``.
Refs resolve to full SHAs before cache lookup. ``WORKTREE`` explicitly selects
working files; dirty collection requires ``--allow-dirty`` and bypasses reuse.

Declare helper functions with ``dependencies``, external files/directories
with ``inputs``, toolchain versions with ``tools``, and execution variables
with ``environment``. Relative input paths are read at the target commit;
absolute inputs are hashed from the filesystem. The library cannot infer
undeclared globals, dependencies, or external services.

An optional ``build(context) -> Path`` receives ``build_config`` and prepares
``context.binary``. Builds share work across compatible treatments in an
invocation. Entirely cached evaluations perform no builds or worktree creation.

Select treatments with ordinary Python
--------------------------------------

An output's ``requires(treatments)`` function returns the treatments it needs.
It receives all registered treatments as read-only dictionaries. Return
members of that catalog, or equivalent copies; invented configurations are
rejected. Lists, generators, comprehensions, and reusable functions all work.
Omitting ``requires`` selects the whole catalog. No selection DSL is needed.

.. doctest::

   >>> import eval_live
   >>> treatments = [{"suite": suite, "size": size} for suite in ("a", "b", "c") for size in (10, 100)]
   >>> needed = lambda ts: [t for t in ts if t["suite"] in {"a", "b"}]
   >>> [t["size"] for t in needed(treatments)]
   [10, 100, 10, 100]

.. code-block:: python

   def summary(data):
       return [{"observations": len(data.get("measurements", []))}]

   reg.table("Selected suites", summary, requires=needed)
   reg.table("All treatments", summary)  # everything declared
   reg.run(args=args, argv=tokens)

Only selected outputs' selectors are evaluated. Their results are combined
and deduplicated before collection. Overlapping selections and equivalent
treatments share one cached observation. An explicit empty selection requires
no collection and remains replayable later.

Callbacks keep the original Registry interface: ordinary dictionaries of
named tables. During ``reg.run`` they contain precisely the observations
selected for that output. ``eval_runs`` and ``eval_configs`` provide provenance.
``eval_requirements`` names its requirements, including empty ones, while
``eval_selections`` links names to run IDs. Rows are copied so callbacks cannot
change the saved snapshot.

For comparisons, name selector functions and use ``select(data, name)``:

.. code-block:: python

   reg.graph("History", history, requires={
       "baseline": lambda ts: [t for t in ts if t["commit"] == "v1"],
       "candidate": lambda ts: [t for t in ts if t["commit"] == "HEAD"],
   })

.. doctest::

   >>> snapshot = {"measurements": [{"run_id": "a", "seconds": 0.2}],
   ...             "eval_selections": [{"requirement": "candidate", "run_id": "a"}]}
   >>> candidate_data = eval_live.select(snapshot, "candidate")
   >>> candidate_data["measurements"][0]["seconds"]
   0.2
   >>> candidate_data["measurements"].clear()
   >>> len(snapshot["measurements"])
   1

Graphs return matplotlib Figures; tables return lists of row dictionaries,
including styled cells. ``reg.macros(name, fn)`` registers a callback returning
LaTeX macro names and scalar values. Macro names contain letters only; values
are escaped. Filenames and ``--only`` use the display name's slug, or explicit
``id=`` values. Output IDs and titles must be unique for ``reg.run``.

Repetitions, keys, and CLI configuration
----------------------------------------

Repetitions are ordinary treatments. The script owns its repetition option:

.. code-block:: python

   parser = eval_live.add_arguments(argparse.ArgumentParser())
   parser.add_argument("--samples", type=int, default=5)
   args = parser.parse_args(tokens)
   reg.benchmark("solver", run_solver, version="1", treatments=[
       {"suite": suite, "sample": i}
       for suite in suites for i in range(args.samples)
   ])

There is no library sample counter or statistics API. ``sample`` is just a
user-defined treatment field. A callback can compute means, medians, or any
other aggregate across the selected rows using ordinary Python.

All measurement-affecting CLI options participate in cache identity by default,
including a script's ``--samples`` count. Both the total count and the individual
sample index distinguish results. Changing five samples to ten therefore
collects ten fresh observations per configuration; repeating ten reuses them.
Every resolved argument/default and original argv token is saved.

Standard output/selection controls such as ``--output`` and ``--only`` do not
change measurements. Paper-specific exclusions must be declared explicitly
with ``Registry(selection_arguments=[...])``. The demo excludes the whole
``sizes`` list because each individual size is already a treatment field. It
keeps the total sample count in the configuration key.

The cache also includes repository/full commit, collection and declared helper
code, declared data version, input hashes, and relevant environment/tool
settings. Equivalent treatments deduplicate even when returned by different
selectors. Plot code and selectors do not affect measurement identity.

A treatment plus CLI configuration and commit identifies what to collect. A
table that stores multiple rows for that treatment may additionally distinguish
rows by phase or another domain key. ``run_id`` links rows to the recorded
attempt; forced runs retain history. Derived averages can group away the sample
field. These table-specific choices belong to the paper's callbacks.

Version the collected tables
----------------------------

``reg.benchmark(..., version="2")`` declares the data format/measurement protocol
version for the tables that callback returns. It is stored explicitly in
``eval_runs.data_version`` and included in the runner fingerprint. Bump it when
tables change shape or meaning, or when an otherwise undeclared protocol change
requires recollection. Existing observations retain their original version;
new invocations select data using the current identity. Changes to rendering
alone reuse data. This version is independent of the JSON container's schema.

Timeouts, failures, and replay
------------------------------

``timeout=60`` on a benchmark sets a deadline per command; ``--timeout`` overrides
it. Timeouts terminate the process group on POSIX and preserve captured output.
Arbitrary Python callbacks are not preempted; use ``context.command`` for work
that needs a deadline. Collection is sequential, checkpoints every attempt,
and continues independent treatments after failures.

Timeout is a retry bound, separate from measurement identity. Increasing it
preserves successes and permits retries of timed-out treatments. An equal or
smaller limit leaves cached timeouts blocked. Ordinary failures require
``--force``. The newest applicable attempt determines reuse; older successes
can be reused when a more recent timeout becomes inapplicable. A shorter
limit accepts a success only if its saved total duration fits (conservatively
including setup/build time). Each attempt keeps its original deadline.

``--force`` collects fresh observations while retaining history. Incomplete
collection raises ``CollectionError`` after saving failures and rendering
independent outputs. Interrupted attempts can be retried.

``--render-only`` uses the latest successful invocation, or ``--invocation ID``.
It replays saved selections, including empty requirements, without evaluating
selectors, resolving Git refs, or collecting. The rendering script and its
dependencies must remain available. Use ``--only`` when replaying an invocation
that contained only a subset of outputs. ``--data PATH`` shares a cache across
output directories; use separate directories for subset reports because old
unrelated generated files remain in place.

The original ``run_tables(data)``, ``run_graphs(data)``, terminal renderer, and
``render_to_dir(data, path)`` still use data supplied by the caller and never
collect. The directory renderer preserves table JSON sidecars; ``reg.run``
writes all evaluation data to one JSON file.

Interactive reports
-------------------

The HTML uses the same rendering callbacks and saved selections. Rendering
and filter callbacks must be named top-level functions in one source file,
or supply ``browser_source=Path(__file__)`` explicitly. Keep execution under
``if __name__ == '__main__'`` and execution-only imports inside collection
callbacks or the entry point. Registration is safe during browser imports.
Treatment selectors can be lambdas or closures: replay does not execute them.

``filter_source(rows, data)`` keeps the original contract: return a data
dictionary with raw tables narrowed to visible computed rows. Preserve metadata
with ``{**data, ...}`` when replacing tables. Filters chain in registration order.
Handle empty filtered data in graph/table callbacks.

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: narrow_summary

Open the HTML directly. Raw tables need no Python runtime; computed tables and
graphs fetch Pyodide and matplotlib from a pinned CDN. The browser never runs
benchmarks. The demo's complete registry setup is:

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: main
