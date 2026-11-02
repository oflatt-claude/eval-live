Migrating existing Registry scripts
===================================

Keep your registry, graph/table callbacks, and filters. The same ``Registry``
now handles collection and caching. Existing ``css()``, ``js()``,
``pyodide_lib()``, terminal rendering, and ``render_to_dir`` remain available.

To adopt collection and caching:

1. Register a collector with ``reg.benchmark(..., version=..., error_rows=...)``.
   It returns named flat tables. Split repeated samples and nested timing
   summaries into rows, and declare inputs, helper dependencies, tool versions,
   and measurement parameters.
2. Pass configuration dictionaries as ``treatments=`` to ``reg.benchmark``.
   Outputs use all declared treatments by default. Add ``requires=selector``
   to existing graph/table registrations when only a subset is needed; the
   Python function returns catalog members. Named selector functions support
   comparisons. Callbacks still receive ``data``; filters return narrowed data.
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

   def error_rows(treatment, status):
       return {"measurements": [{"sample": treatment["sample"], "seconds": None, "status": status}]}

   reg = eval_live.Registry()
   reg.benchmark("worker", collect, version="1", error_rows=error_rows, inputs=["worker.py"],
                 treatments=[{"sample": i} for i in range(args.samples)])
   reg.table("Summary", summary)
   reg.run(args=args, argv=tokens)

The script defines its own ``--samples`` argument. Both that CLI count and
the individual treatment's sample index affect cache identity by default.
Compute averages in callbacks. The declared ``version`` is saved with every
observation and invalidates reuse when changed.

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
linked by ``run_id``. See ``demo/egglog_eval.py`` for an adapter using one
treatment list and one collector.

.. code-block:: bash

   python demo/egglog_eval.py --repository /path/to/egglog-encoding --binary /path/to/egglog-experimental --samples 1

``--binary`` selects the executable; ``--repository`` and ``--commit`` select
the ``.egg`` input files. The script declares a cross product of files, modes,
and samples, then registers one collector that runs any of those treatments.
``--modes`` selects off/proofs/proof-testing commands. The collector returns
timing, phase, and ruleset tables from schema-4 timing summaries. Repeating the command reuses exact observations; changing the
binary or workload content invalidates them.

Data-file compatibility
-----------------------

Earlier schema-1 files remain readable. Their attempts may lack
``data_version`` or ``has_rows`` and may contain a legacy ``sample`` index.
The loader preserves those rows and reconstructs named requirements from
saved selections when necessary. It also accepts earlier cache keys that
included the timeout. Adding an ``error_rows`` callback changes the collection
identity, so adopting the row contract collects fresh observations.

See :doc:`python-guide` for optional timeouts, parallel collection, and computed
warnings/errors. No timeout is applied by default. Figure messages are
computed during rendering and are never added to the saved measurements.
