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
   python demo/eval.py --timeout 60            # opt into a 60-second command limit
   python demo/eval.py --render-only           # regenerate from saved selections

The demo creates a tiny local Git repository with two commits. No clone or
compiler is needed. It writes one ``demo/out/paper/results.json`` data/cache
file plus ``runtime.pdf``, ``history.pdf``, ``summary.tex``, ``macros.tex``, and
``index.html``. The sibling lock file is writer coordination, not stored data.
A first-ever dry-run requires creating the workload repository by running the demo.

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

   def error_rows(treatment, status):
       return {"measurements": [{"suite": treatment["suite"], "size": treatment["size"],
                                 "seconds": None, "status": status}]}

   reg = eval_live.Registry()
   reg.benchmark(
       "worker", collect, version="1", error_rows=error_rows, inputs=["worker.py"],
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

``repository`` defaults to the current directory. Requested treatments without
``commit`` use the CLI's ``--commit`` (HEAD by default). Treatments can override
the repository; output selectors can add or change their commit.
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
It receives all registered treatments as read-only dictionaries, with
``benchmark`` and ``repository`` added. Return those treatments or copies;
copies may add, change, or omit ``commit``. Other configuration fields must
match a declared treatment. Lists, generators, comprehensions, and reusable
functions all work. Omitting ``requires`` selects the whole catalog.

Commit defaults are applied after selection: any requested treatment without
``commit`` uses ``--commit`` (HEAD by default). A figure opts into another
commit by returning a copy with that field set. Commit aliases resolving to
the same observation share work and cached data.

.. doctest::

   >>> import eval_live
   >>> treatments = [{"suite": suite, "size": size} for suite in ("a", "b", "c") for size in (10, 100)]
   >>> needed = lambda ts: [t for t in ts if t["suite"] in {"a", "b"}]
   >>> [t["size"] for t in needed(treatments)]
   [10, 100, 10, 100]

.. code-block:: python

   @reg.table("Selected suites", requires=needed)
   def summary(data):
       return [{"observations": len(data.get("measurements", []))}]
   reg.table("All treatments", summary, id="all_treatments")  # everything declared
   reg.run(args=args, argv=tokens)

Only selected outputs' selectors are evaluated. Their results are combined
and deduplicated before collection. Overlapping selections and equivalent
treatments share one cached observation. An explicit empty selection requires
no collection and remains replayable later.

Callbacks receive dictionaries of named tables containing the observations
selected for that output. Use ``eval_runs`` and ``eval_configs`` for provenance;
the :doc:`data-format` reference describes their fields. Callback inputs are
copies, so changing them does not change the saved data.

For a comparison, request the same treatments at multiple commits:

.. code-block:: python

   reg.graph("History", history, requires=lambda ts: [
       request for treatment in ts
       for request in (treatment, {**treatment, "commit": "v1"})
   ])

The resolved commit is stored in ``eval_runs``; figure callbacks choose legend
labels and join measurements to commits using ``run_id``.
See the demo's comparison callback:

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: history

Named selectors can make it easier to retrieve each group with
``select(data, name)``. These names identify output requirements and do not
become treatment fields or affect cache identity:

.. code-block:: python

   reg.graph("History", history, requires={
       "baseline": lambda ts: [{**t, "commit": "v1"} for t in ts],
       "candidate": lambda ts: ts,
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

Use ``@reg.graph(...)``, ``@reg.table(...)``, ``@reg.macros(...)``, or
``@reg.benchmark(...)`` to register a function where it is defined. Decorators
return the original function, so callbacks can still call each other. The
direct ``reg.table(name, function)`` form is useful
when treatments depend on CLI arguments parsed in ``main``.

Graphs return matplotlib Figures; tables return lists of row dictionaries,
including styled cells. ``reg.macros(name, fn)`` registers a callback returning
LaTeX macro names and scalar values. Macro names contain letters only; values
are escaped. Table filenames and ``--only`` use the callback's function name;
graph and macro IDs use the display title's slug. Explicit ``id=`` values
override either default. Output IDs and titles must be unique for ``reg.run``;
give distinct IDs when registering one table function more than once.

Repetitions, keys, and CLI configuration
----------------------------------------

Repetitions are ordinary treatments. The script owns its repetition option:

.. code-block:: python

   parser = eval_live.add_arguments(argparse.ArgumentParser())
   parser.add_argument("--samples", type=int, default=5)
   args = parser.parse_args(tokens)
   reg.benchmark("solver", run_solver, version="1", error_rows=error_rows, treatments=[
       {"suite": suite, "sample": i}
       for suite in suites for i in range(args.samples)
   ])

``sample`` is a user-defined treatment field. A callback can compute means, medians, or any
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
settings. Plot code and selectors do not affect measurement identity.

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

Opt in to parallel collection
-----------------------------

Treatments run sequentially by default. Add ``"parallel": True`` to treatments
that can safely run together:

.. code-block:: python

   treatments = [{"suite": suite, "parallel": True} for suite in suites]
   reg.benchmark("solver", run_solver, version="1", error_rows=error_rows, treatments=treatments)

Marked treatments run in separate Python processes, using at most ``--jobs N``
workers (default: ``os.cpu_count()`` or 1). This supports CPU-bound Python as
well as external commands. ``--jobs 1`` runs everything sequentially. Unmarked
treatments, including ``parallel=False``, run alone: pending workers finish
before they start, and subsequent workers wait for them to finish.

Parallel collectors must be importable top-level functions; guard script
execution with ``if __name__ == '__main__'``. Each committed parallel treatment
gets its own temporary worktree. Build outputs are shared and should be treated
as read-only; absolute external paths and ``WORKTREE`` files remain shared.
Only mark treatments whose shared resources allow concurrent use.

Concurrency can affect measurements, so the flag and resolved worker limit
are saved with the configuration and affect cache identity.

Both demos expose ``--parallel`` to mark their treatment lists. For example:

.. code-block:: bash

   python demo/eval.py --parallel --jobs 4

Complete rows, timeouts, and errors
-----------------------------------

**Invariant: every requested treatment has rows in every table declared by
``error_rows`` before collection begins.** A timeout, failure, or stopped run
never silently removes a treatment from the database or a figure's input.

Register ``error_rows(treatment, status)`` alongside the collector. It is a pure
function returning named flat tables, with at least one row in each table. It
receives the same configuration fields as ``context.config``. The library
validates its rows for all statuses before any collection or database writes:

.. code-block:: python

   def error_rows(treatment, status):
       return {"measurements": [{"suite": treatment["suite"],
                                 "seconds": None, "status": status}]}

   reg.benchmark("solver", collect, version="1", error_rows=error_rows,
                 treatments=[{"suite": "a"}, {"suite": "b"}])

The statuses are ``pending``, ``timed-out``, ``failure``, and ``interrupted``.
Use explicit status values and null measurements; a timeout and an execution
error mean different things. Successful collectors return their ordinary rows,
usually with ``status="success"``. They must include rows in every declared
table. Additional per-phase or per-rule rows belong to the collector.

Successful measurements replace pending rows; timeouts and failures replace
them with the corresponding error rows.
``error_rows`` must declare the same tables for every status. Empty or invalid
collector results become failure rows. Changing ``error_rows`` affects cache
identity, just like changing collection code.

Timeouts are **disabled by default**. Opt in with ``--timeout SECONDS`` or
``timeout=SECONDS`` on the benchmark. The deadline applies separately to each
``context.command`` call, including build commands. On POSIX a timeout kills
the subprocess group. Arbitrary Python callbacks are not preempted. Captured
stdout, stderr, and execution status remain available with each attempt.

Repeating the same deadline reuses timeout rows. Increasing it, or returning
to an unlimited deadline, recollects timed-out treatments and preserves
successes. Ordinary failures reuse their error rows until ``--force`` requests
fresh collection. Timeouts and failures do not automatically produce warnings,
reporting errors, or a failing CLI exit status.

Ctrl+C stops the whole evaluation: active workers and their commands are
cancelled on POSIX, queued treatments do not start, and figure generation is
skipped. Completed rows remain saved; unfinished treatments keep explicit
interrupted rows and are eligible for collection on the next invocation.
After an abrupt process crash, the already-saved placeholders still provide
row coverage. ``--force`` retains earlier attempts as history.

Computed warnings and errors
----------------------------

Warnings and errors are rendering outputs, computed from the selected rows.
They are never stored in the measurement database and never affect its cache.
A typical figure distinguishes timeouts from execution failures, excludes null
measurements from averages, and decides which conditions deserve a message:

.. code-block:: python

   @reg.table("Summary")
   def summary(data):
       rows = data["measurements"]
       if any(row["status"] == "timed-out" for row in rows):
           eval_live.warn("Some treatments timed out; averages use completed runs.")
       if any(row["status"] == "failure" for row in rows):
           eval_live.error("Some treatments failed; inspect their diagnostics.")
       completed = [row["seconds"] for row in rows if row["status"] == "success"]
       return [{"requested": len(rows), "completed": len(completed),
                "mean_seconds": sum(completed) / len(completed) if completed else None}]

This policy belongs to the paper: a timeout may be an expected result worth
plotting, while an unexpected failure may justify a reporting error. The
library does not impose either policy. Normal error/timeout rows remain usable
by every dependent output.

``eval_live.warn(message)`` and ``eval_live.error(message)`` show messages as
callbacks run. Identical level/message pairs are deduplicated across outputs.
The CLI ends with all warnings followed by all errors. Warnings do not fail
the command; reporting errors give a failing exit status after the remaining
outputs have rendered. An exception in a figure is also a reporting error and
does not prevent the other figures from running.

The interactive page shows derived Warnings and Errors tables. Messages update
with filtered data; they are not appended to the raw tables. ``--render-only``
recomputes messages, so changing figure policy needs no new measurements.

Replay
------

``--render-only`` uses the latest invocation with completed collection, or
``--invocation ID``. It replays saved selections, including empty requirements, without evaluating
selectors, resolving Git refs, or collecting. The rendering script and its
dependencies must remain available. Use ``--only`` when replaying an invocation
that contained only a subset of outputs. ``--data PATH`` shares a cache across
output directories; use separate directories for subset reports because old
unrelated generated files remain in place.

``run_tables(data)``, ``run_graphs(data)``, the terminal renderer, and
``render_to_dir(data, path)`` use data supplied by the caller and never
collect. The directory renderer writes table JSON sidecars; ``reg.run``
writes all evaluation data to one JSON file.

Interactive reports
-------------------

The HTML uses the same rendering callbacks and saved selections. Rendering
and filter callbacks must be named top-level functions in one source file,
or supply ``browser_source=Path(__file__)`` explicitly. Keep execution under
``if __name__ == '__main__'`` and execution-only imports inside collection
callbacks or the entry point. Registration is safe during browser imports.
Treatment selectors can be lambdas or closures: replay does not execute them.

``requires`` selects treatments before collection and limits each rendering
callback to its selected data. Browser filtering does not change these
requirements or collect more data.

Register ``@reg.table("Summary", interactive_filter=narrow_summary, ...)`` to
propagate visible computed rows to other tables and figures. The callback receives
those rows and the shared dataset across all outputs, after raw-table filters
and preceding interactive filters. It returns a dictionary with raw tables
narrowed to matching rows. Preserve other tables and metadata with
``{**data, ...}``. Filters chain in registration order. Handle empty filtered
data in graph/table callbacks.

For example, a summary of the current commit can filter measurements by size
across both baseline and current commits. A comparison figure then receives
the matching rows within its own requirements. Filters can also use measured
or computed values, such as a summary's median runtime.

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: narrow_summary

Open the HTML directly. Raw tables need no Python runtime; computed tables and
graphs fetch Pyodide and matplotlib from a pinned CDN. The browser never runs
benchmarks. Outputs are registered with decorators above their callbacks; the
demo's entry point declares treatments and starts collection:

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: main
