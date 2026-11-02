Paper evaluation refactor plan
==============================

Purpose and scope
-----------------

Extend the existing ``Registry`` so a single Python script can collect the
required data and generate all paper figures, tables, macros, and interactive
HTML. Running without arguments executes the complete evaluation. The current
implementation follows this plan; :doc:`python-guide` and :doc:`api` describe
its public contracts.

Keep the library small. Use ordinary dictionaries for treatments, Python
functions for selection, and argparse for configuration. Repetitions and
statistics belong to the paper script. Collection is sequential and uses one
JSON file containing named flat tables. Parallel execution, streaming/database
backends, full dirty-source snapshots, and hosted docs are deferred.

Reference implementation
------------------------

The design borrows the workflow from ``saulshanabrook/egglog-encoding``:

* `benchmarking/benchmark.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/benchmark.py>`_:
  one CLI for selection, collection, and reporting.
* `benchmarking/collection.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/collection.py>`_
  and `targets.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/targets.py>`_:
  determine missing work first, isolate historical revisions, share builds,
  and preserve completed observations.
* `benchmarking/reports/store.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/reports/store.py>`_:
  explicit identities, provenance, and retained attempt history.
* `PR #151
  <https://github.com/saulshanabrook/egglog-encoding/pull/151>`_:
  raising timeout permits retries while preserving successful observations.

Generalize these mechanisms without adopting the pair-specific CLI, Rust build
commands, treatment enums, nested timing summaries, or multiple data stores.

Public model
------------

One ``Registry`` owns collection callbacks and outputs. Preserve the original
``graph(name, fn)`` and ``table(name, fn, filter_source=...)`` signatures and
data-dictionary callbacks. Add ``benchmark`` to declare a collection callback
and its catalog, ``macros`` for LaTeX definitions, and ``plan``/``run`` for local
orchestration. There is no separate evaluation object or treatment class.

.. code-block:: python

   reg = Registry()
   reg.benchmark("solver", collect, version="1", treatments=[
       {"suite": suite, "mode": mode, "sample": sample}
       for suite in suites for mode in modes for sample in range(args.samples)
   ])
   reg.graph("Proof timings", plot, requires=lambda ts: [
       t for t in ts if t["suite"] in {"a", "b"} and t["mode"] == "proofs"
   ])
   reg.table("Summary", summary)  # all treatments by default
   reg.macros("Paper numbers", numbers)
   reg.run(args=args, argv=tokens)

Treatments are configuration dictionaries. The catalog is finite and may be
a cross product or an arbitrary list. Each treatment specifies or inherits a
repository and Git ref. Selector functions receive read-only catalog rows
including ``benchmark``, ``repository``, and ``commit``. They return catalog
members or equivalent copies. There is no filter DSL. No selector means the
entire catalog; an explicit empty result requires no collection.

Outputs can name selectors for comparisons. ``select(data, "baseline")``
extracts the corresponding rows inside a callback. The ordinary tables contain
the union of that output's selected observations. The same observations feed
files, terminal output, and browser rendering. Selectors can be closures; only
render/filter callbacks need to be importable in the browser.

Configuration, keys, and versions
---------------------------------

Save every CLI argument and default plus the original argv tokens. Include
measurement-affecting arguments in cache identity automatically; standard
output/selection controls are excluded. Paper-specific exclusions are explicit
``selection_arguments``. A total sample count remains a normal configuration
argument by default.

There is no built-in sampling loop. A paper that wants repetitions declares
``sample`` as a treatment field and builds that dimension in Python. Both its
sample index and a CLI total count affect the key. Changing a count from five
to ten therefore collects ten fresh results per configuration. User callbacks
compute averages and other statistics across selected rows.

The reusable identity contains repository/full commit, complete treatment,
CLI measurement configuration, collection/helper code, declared data version,
inputs, and relevant environment/tool settings. Resolve refs before lookup;
equivalent treatment selections share one observation. Rendering changes do
not invalidate measurements.

Each returned table row references its collecting attempt via ``run_id``.
That attempt links to configuration and commit metadata. A table may add domain
keys such as phase when it returns multiple rows for one treatment; averages
can group away sample indices. Retaining attempt IDs preserves forced-run and
failure history without confusing the observations selected for a report.

``benchmark(..., version="2")`` declares the returned tables' data/protocol
version. Record it in ``eval_runs.data_version`` and include it in identity.
Changing it invalidates cached observations while preserving older data. This
version is separate from the JSON container's schema version.

Storage and execution
---------------------

* Use one JSON dictionary of flat tables. Cells are finite scalars; repeated
  values become rows. Normalize CLI/configuration lists into typed rows, and
  reserve ``eval_`` names for provenance. HTML embeds a snapshot of the same data.
* Validate identities, parameter encodings, and foreign keys before collection.
  Checkpoint atomically after each attempt under one writer lock. Preserve
  incompatible data and fail clearly. See :doc:`data-format` for table contracts.
* Select outputs and evaluate their selectors before resolving execution
  targets. Union and deduplicate requirements, find cache hits, then collect
  missing treatments. Default output selection is the entire registry.
* Use library-owned detached worktrees for committed targets. Never switch the
  user's checkout. Explicit dirty WORKTREE runs bypass persistent reuse.
* Share optional builds within an invocation. Store binary hashes and command
  diagnostics. Entirely cached runs perform no builds or worktree creation.
* Checkpoint failed and interrupted attempts; continue independent treatments.
  Render only outputs whose requirements are complete and report incomplete
  evaluations as failures. Forced attempts retain history.
* Treat timeout as a retry bound, not a measurement setting. Raising it permits
  timeout retries and preserves successes; unchanged/smaller bounds keep cached
  timeouts blocking. Ordinary failures require force. Deadlines remain attached
  to their original attempts and apply to subprocesses, not arbitrary callbacks.
* Store exact selected run IDs and declared requirement names, including empty
  selections. Render-only replays those choices without Git, selectors, or
  collection. Dry-run performs no writes or measured execution.

Implementation and acceptance
-----------------------------

1. Document the paper-evaluation workflow and retain the familiar Registry API.
2. Implement the flat JSON store, complete argument capture, provenance, data
   versions, atomic checkpoints, and exclusive writers.
3. Add isolated Git execution, declared-input fingerprints, optional shared
   builds, command diagnostics, timeout retries, and exact caching.
4. Plan from Python selectors over finite treatment catalogs. Verify default
   whole-catalog selection, cross-product subsets, overlapping/equivalent rows,
   named comparisons, empty selections, and replay without selectors.
5. Reuse one output catalog and file renderer. Preserve original Registry
   callbacks, styled cells, and chained filters. Verify PDF/LaTeX/macros and
   actual browser/Pyodide behavior use the same selected observations.
6. Supply a small two-commit top-level demo and a real egglog adapter. Demonstrate
   cache hits, changed CLI parameters, timeout retries, and historical comparisons.
7. Build Python tutorial/API/schema/migration docs and doctests in CI alongside
   Python integration tests and JavaScript/browser checks.

Implementation stays in the existing browser-compatible ``eval_live.py`` and
local ``store.py``, ``benchmarks.py``, ``targets.py``, ``planning.py``,
``render.py``, and ``cli.py`` modules. Registration remains browser-safe;
collection is imported only by local execution.
