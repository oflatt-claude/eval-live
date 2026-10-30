Migrating existing Registry scripts
===================================

The original ``Registry``, ``css()``, ``js()``, ``pyodide_lib()``, terminal
renderer, and ``render_to_dir`` remain available. Existing callbacks taking a
data dictionary continue to work. The new workflow is additive.

To adopt collection and caching:

1. Move collection into ``Benchmark.run(context)`` and return named flat tables.
   Split repeated samples and nested timing summaries into rows. Declare inputs,
   helper dependencies, tool versions, and measurement parameters.
2. Replace orchestration with an ordered list of ``Figure``, ``Table``, and
   ``Macros`` definitions. Give each output explicit named ``RunRequest``
   requirements. Existing single-scope callbacks can be adapted with a named
   wrapper that calls ``old_callback(view.data)``.
   Artifact filter callbacks return matching run IDs; the legacy Registry's
   filter callbacks continue to return narrowed data dictionaries.
3. Use ``add_arguments`` with ordinary argparse options and pass resolved args
   plus original tokens to ``Evaluation.run``. Put collection/API construction
   inside ``main`` and guard execution for browser imports.
4. Let ``Evaluation`` persist one JSON file and generate the HTML and paper
   artifacts. The new export path does not produce per-table JSON sidecars;
   legacy ``Registry.render_to_dir`` retains its existing behavior.

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
