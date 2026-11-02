Migrating existing Registry scripts
===================================

Keep your registry, graph/table callbacks, and filters. The same ``Registry``
now handles collection and caching. Existing ``css()``, ``js()``,
``pyodide_lib()``, terminal rendering, and ``render_to_dir`` remain available.
There is no separate evaluation object or artifact class to construct.

To adopt collection and caching:

1. Register a callback with ``reg.benchmark(name, collect, version=..., ...)``.
   It returns named flat tables. Split repeated samples and nested timing
   summaries into rows, and declare inputs, helper dependencies, tool versions,
   and measurement parameters.
2. Add ``requires=`` to existing ``reg.graph`` and ``reg.table`` calls. It takes
   a ``RunRequest``, a list, or named requirements for comparisons. Callbacks
   still receive ``data``; filters still return narrowed data dictionaries.
3. Use ``add_arguments`` with ordinary argparse options and pass resolved args
   plus original tokens to ``reg.run``. Guard execution inside ``main`` for
   browser imports; registration itself does not run benchmarks.
4. Let ``reg.run`` persist one JSON file and generate HTML and paper artifacts.
   It writes no per-table JSON sidecars; ``reg.render_to_dir(data, path)`` keeps
   its existing behavior when rendering data you supply.

For example, only registration and execution change for an existing callback:

.. code-block:: python

   def summary(data):
       return [{"samples": len(data["measurements"])}]

   reg = eval_live.Registry()
   reg.benchmark("worker", collect, version="1", inputs=["worker.py"])
   reg.table("Summary", summary,
             requires=eval_live.RunRequest("worker", samples=args.samples))
   reg.run(args=args, argv=tokens)

Use ``reg.macros`` for paper numbers. For cross-commit comparisons, name the
requirements and call ``eval_live.select(data, "baseline")`` inside the
callback. Explicit ``id=`` values keep filenames stable when titles change.

Old arbitrary raw JSON remains loadable in the standalone viewer. It is not a
valid new cache: commit, configuration, and selection provenance cannot be
inferred safely. Use a new results file and recollect, or write a deliberate
project-specific importer with complete provenance.

For an egglog-style evaluator, keep engine commands and timing-summary parsing
in benchmark callbacks. Replace pair-specific orchestration with named
requirements, and normalize per-rule/per-phase details into additional tables
linked by ``run_id``. The generic runner has no Rust-specific build logic or
hard-coded treatment names. See ``demo/egglog_eval.py`` for a small real-engine
adapter using the same API.

.. code-block:: bash

   python demo/egglog_eval.py --repository /path/to/egglog-encoding --binary /path/to/egglog-experimental --samples 1

This example selects committed workload files and fingerprints a separately
supplied engine binary. It does not assert that the binary was built from the
workload's commit. It adapts the reference evaluator's off/proofs/proof-testing
commands and normalizes schema-4 timing summaries into timing, phase, and
ruleset tables. Repeating the command reuses exact observations; changing the
binary or workload content invalidates them.
