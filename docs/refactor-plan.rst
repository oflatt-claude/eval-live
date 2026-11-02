Paper evaluation refactor plan
==============================

Status and scope
----------------

This document records the design and implementation sequence for the refactor. The store, benchmark runner, cache, planner, registry exports,
demo, and Python documentation are now implemented. The current contracts and
examples are in :doc:`python-guide` and :doc:`api`. JSON containing named flat
tables is the chosen storage format. Parallel execution, full dirty-source
snapshots, and hosted documentation remain outside this first version.

The timeout policy below reflects the subsequent request to follow
`egglog-encoding PR #151
<https://github.com/saulshanabrook/egglog-encoding/pull/151>`_: deadlines are
recorded with observations but excluded from their measurement identity.

The public API extends the original Registry. Benchmarks, graphs, tables, and
macros share one catalog and ordinary data-dictionary callbacks; collection
remains a private runner behind ``Registry.run``.

The author writes one Python entry point. Running it with no arguments
produces every paper figure, table, macro, and an interactive HTML report.
Arguments change parameters or select artifacts and benchmark subsets. A
catalog of artifacts declares the data requirements; eval-live unions those
requirements, reuses matching observations, collects missing observations,
and renders the selected artifacts.

What to borrow from egglog-encoding
------------------------------------------

The reference is ``saulshanabrook/egglog-encoding`` on ``main``, inspected on
2026-10-30. The relevant pieces are:

* `benchmarking/benchmark.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/benchmark.py>`_:
  a single CLI composes selection, collection, and reporting.
* `benchmarking/collection.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/collection.py>`_:
  collection plans count reusable and missing observations before execution,
  share builds across endpoints, and preserve partial results.
* `benchmarking/reports/store.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/reports/store.py>`_:
  exact identities, explicit schema versions, and one observation per record.
  Its cache includes binary and input hashes, treatment, timeout, and other
  execution settings; commit names alone would be insufficient.
* `benchmarking/targets.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/targets.py>`_:
  resolve refs to commits, materialize detached worktrees, and build once per
  target. Keep the general mechanism and make builds user callbacks.
* `benchmarking/reports/catalog.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/reports/catalog.py>`_
  and `interactive.py
  <https://github.com/saulshanabrook/egglog-encoding/blob/main/benchmarking/reports/interactive.py>`_:
  share presentation definitions across outputs and embed data/code for browser
  exploration without running benchmarks there.

Generalize these ideas rather than copying the pair-only CLI, Rust build
commands, treatment enums, nested timing summaries, or separate JSONL and
grouped snapshots. A paper may compare any number of commit/configuration
pairs. Repeated samples and per-phase timings become separate flat rows.

The pre-refactor foundation was smaller: ``Registry`` knew graphs,
tables, filtering, and local rendering, but assumed data was already collected.
It had no execution or persistence layer. Its browser core is deliberately
small enough to load as one Python module under Pyodide.

Public model
------------

``Registry``
   The script's benchmark definitions and ordered output catalog. The existing
   ``graph(name, fn)`` and ``table(name, fn, filter_source=...)`` methods accept
   optional ``requires=``. Add ``benchmark`` and ``macros`` registrations,
   ``plan`` to inspect work, and ``run`` to collect, checkpoint, and render.
   Ordinary argparse remains the CLI. Existing renderers still accept data
   supplied by the caller.

``RunRequest``
   Benchmark, repository, commit/ref, resolved configuration, and required
   sample count. Resolve refs to full SHAs before looking up observations.
   Requests are immutable after resolution. ``requires`` accepts one request,
   a sweep, or a mapping of named requirements.

``select(data, name)``
   Pick one named requirement from the dictionary passed to an output callback.
   Without this helper, a callback sees the union of its required observations.
   Filters retain the original ``filter_source(rows, data) -> data`` contract.

Example (see the Python guide and demo for complete runnable scripts):

.. code-block:: python

   reg = Registry(selection_arguments=["sizes"])
   reg.benchmark("membership", membership, version="1", inputs=["membership.py"])
   baseline = RunRequest("membership", commit=args.baseline,
                         config={"algorithm": "set"}, samples=args.samples)
   candidate = RunRequest("membership", commit=args.commit,
                          config={"algorithm": "set"}, samples=args.samples)
   reg.graph("Runtime", runtime, requires=candidate)
   reg.table("Comparison", compare,
             requires={"baseline": baseline, "candidate": candidate})
   reg.macros("Paper numbers", numbers, requires=candidate)
   reg.run(args=args, argv=argv)

Register outputs directly; no separate Evaluation or Figure/Table/Macros
classes are needed. The same catalog is used for planning, files, terminal
rendering, and the browser. Benchmark code is imported lazily so the browser
still uses a standalone Python module. Do not infer dependencies by executing
plots.

Storage and reproducibility
---------------------------

Use one ``results.json`` dictionary of named tables. It is both the persistent
record and the benchmark cache. Every table is a list of rows; every cell is a
string, finite number, boolean, or null. No nested cell objects, sample arrays,
or JSON-encoded configuration blobs. Reserve an ``eval_`` prefix for metadata.
Rendered styled cells remain presentation values and are not raw observations.

The first schema should include:

======================= ======================================================
Table                   Purpose
======================= ======================================================
``eval_metadata``       Singleton schema version and writer version.
``eval_invocations``    Invocation ID, timestamps, cwd, script/source identity,
                        interpreter, platform, and final status.
``eval_argv``           Invocation ID, token position, and original CLI token.
``eval_arguments``      Invocation ID, argument name, item index, value type,
                        and scalar value, including all resolved defaults.
``eval_configs``        Config ID, parameter name, item index, type, and value.
``eval_runs``           Run/attempt ID, collecting invocation, benchmark,
                        commit, config ID, input/runner/environment hashes,
                        sample index, timing, status, and diagnostics.
``eval_selections``     Invocation, artifact, requirement name, and selected
                        run IDs, including reused observations.
User tables             Measurements linked to runs by ``run_id``; additional
                        per-phase/per-rule rows instead of nested summaries.
======================= ======================================================

List-valued options get one row per element; store an explicit marker for an
empty list so it differs from a missing option or null. Preserve types and
order. Store both the requested Git ref and the resolved SHA. Persist the
evaluation script's revision separately from each benchmarked target's revision.
Hash declared external inputs and record relevant tool/dependency versions
and explicitly configured environment settings. A Git SHA plus flags does
not reproduce modified inputs or a different toolchain.

Validate schemas and foreign keys before running any benchmark. Write to a
temporary file in the destination directory and atomically replace the JSON
after every completed observation. Serialize writes through one coordinator;
reject concurrent writers to the same file in the first version. Temporary
files/locks and disposable build directories are operational state, not extra
data stores. Reject incompatible schemas with an actionable error; never
silently discard old results. Larger streaming/database backends are deferred.

Figures, LaTeX, and HTML are derived artifacts. The new export path should
write no per-table JSON sidecars by default. Preserve the existing
``Registry.render_to_dir`` behavior for compatibility. HTML embeds a snapshot
of the same tables so a reader can explore the report without a server.

Cache and execution semantics
-----------------------------

* Cache identity includes the repository identity and full commit, benchmark
  name/version and runner implementation identity, canonical run configuration,
  declared input hashes, and relevant execution environment. Binary hashes are
  recorded when a build callback produces binaries. Build fingerprints include
  flags, toolchain, and dependency locks so an exact cache hit can avoid a build.
* Store **all** CLI options. Use only explicitly declared execution parameters
  for a reusable observation's configuration. Artifact selection, output path,
  and display format do not change a measurement. Seed, build options,
  and concurrency that affects measurement do. Timeout is a separate retry
  bound, recorded on each attempt. Unknown paper parameters are
  measurement-affecting by default; exclusions must be explicit.
* Sample count is a requirement, not a measurement parameter. Raising it from
  three to five reuses three samples and collects two. Equivalent requests
  across artifacts share work, taking the maximum requested count. Preserve
  the exact sample selection for each rendered invocation.
* Failed, timed-out, or interrupted attempts remain in the file with their
  diagnostics. They never count as successful samples. Stop further samples
  of that failed request during the invocation, continue independent requests,
  and exit nonzero when required data is incomplete. Increasing the timeout
  retries timed-out work and preserves successes. Cached timeouts at an equal
  or smaller bound and ordinary failures block retries; a force option
  collects fresh attempts without
  deleting history. Render dependent artifacts only when their requirements
  are satisfied unless they explicitly support incomplete data.
* Default reusable collection uses clean committed targets. Explicitly allowed
  dirty-worktree runs are marked nonreproducible and bypass persistent reuse;
  never let them share a clean commit's cache identity. Full dirty source
  snapshots can be a later addition.
* Resolve historical commits into library-owned detached worktrees. Never
  check out another revision in the user's working tree. Only build when
  missing observations require execution, isolate build outputs by target,
  and clean up only directories owned by this invocation.
* Start with sequential collection so timings are understandable. Add bounded
  concurrency later as an explicit measurement setting. Rendering and Pyodide
  must remain independent of Git, subprocesses, and filesystem collection.

Planning and CLI behavior
-------------------------

Plan in this order: parse and persist arguments, resolve selected artifacts,
expand their requirements, validate names/configurations, resolve commits and
inputs, deduplicate requests, select cached samples, and identify missing work.
Only then materialize/build targets and execute. Select before collecting, so
``--only comparison`` cannot accidentally run unrelated benchmarks.

The default invocation selects the full artifact catalog. Provide small shared
CLI helpers for output location, artifact/benchmark selection, sample counts,
commit overrides, force, and a dry-run plan. Paper-specific parameters remain
ordinary Python values. A benchmark subset that removes a required input must
produce an explicit incomplete-plan error, not a misleading complete figure.

Dry-run prints required work, cache hits/misses, and why each run is needed,
without builds, subprocess measurements, or data-file writes. A render-only
mode uses stored selections and fails clearly if data is missing. It should
work without the original benchmark repository. Changing labels or plot code
must not recollect valid benchmark observations; measurement-code changes must
invalidate their identity. Keep collection and rendering versions separate.

Implementation sequence and acceptance checks
---------------------------------------------

1. **Document the model and establish the demo.** Add the README philosophy,
   this plan, and a small single-script demo using today's Registry. Add Sphinx
   with a Python tutorial, docstring-generated API reference, and executable
   examples. Explicitly distinguish existing APIs from proposals.

2. **Implement the flat-table store and provenance.** Add typed row contracts,
   validation, canonical configuration encoding, argument capture, atomic JSON
   persistence, and explicit sample selections. Verify round trips preserve
   scalar types, defaults, repeated/empty arguments, and argv order; reject
   nested/non-finite values and incompatible schemas. Interrupted writes must
   leave the last valid file usable.

3. **Add benchmark execution and exact caching.** Introduce run requests,
   target resolution, optional build callbacks, a subprocess helper, and
   resumable per-observation checkpoints. Use a temporary two-commit repository
   and a counting benchmark to prove cache hits execute nothing, parameter or
   input changes miss, historical commits execute the correct code, dirty
   targets cannot reuse clean data, and failed attempts preserve prior results.

4. **Plan from the artifact catalog.** Add named requirements for figures,
   tables, and macros; implement union/deduplication and selection before
   execution. Verify single-commit and cross-commit/configuration views, shared
   dependencies, increasing sample counts, incomplete dependencies, and dry-run
   behavior. Persist the exact reused/fresh sample IDs for later rendering.

5. **Unify paper and browser generation.** Add a local HTML writer, macro
   renderer, and Registry rendering. Render all formats from the same selected
   data. Validate macro names and escape values, check filename collisions,
   and support empty filtered data. Keep the browser runtime free of execution
   imports. Test browser filtering and compare computed numbers with local
   tables/macros; state the CDN requirements explicitly.

6. **Migrate the demo and validate a real evaluation.** Replace the demo's
   argument capture, collection, and page writer with the new helpers. Include
   both a single-config artifact and a comparison artifact, default full run,
   subset run, changed parameters, cache hit, and historical-commit comparison.
   Use a tiny local Git fixture for the two-commit example so it needs no clone
   or expensive compiler. Migrate one egglog-encoding evaluation as an external
   integration exercise before treating the API as stable.

7. **Complete Python documentation alongside each API.** Add type hints,
   parameter/return/error contracts, a guide to authoring benchmark callbacks,
   config/cache semantics, multi-commit figures, flat-table schema, CLI help,
   and migration examples. Include demo source in the docs instead of copying
   it. Add CI jobs for the documentation build, doctests, the no-argument demo,
   cache/planner tests, and existing Python/JavaScript tests. Hosted docs are a
   separate publishing decision; the local build should always work.

Proposed module boundaries are ``store.py``, ``benchmarks.py``, ``targets.py``,
``planning.py``, ``render.py``, and ``cli.py``. Keep
``eval_live/eval_live.py`` as the browser-compatible rendering core while the
local package re-exports the new API. Introduce each module when its phase
needs it, rather than scaffolding the whole design first.

Done means one ordinary paper script can collect only the data its selected
artifacts require, reuse exact observations from the single JSON file, and
regenerate every paper artifact and the interactive report with complete
argument and sample provenance. The demo and Python docs must teach that same
workflow and run as automated checks.
