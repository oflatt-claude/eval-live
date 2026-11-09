Command-line reference
======================

These options are added to an evaluation script's ``argparse`` parser by
:func:`eval_live.add_arguments`. Run the script directly; there is no separate
replay executable. ``python demo/eval.py --help`` also lists that script's own
options, such as ``--samples`` and ``--sizes``.

Collect and generate
--------------------

With no arguments, the script runs all declared outputs. It resolves their
requirements, reuses matching measurements, collects missing treatments, and
regenerates every selected figure, table, macro, and HTML page.

.. code-block:: bash

   python demo/eval.py
   python demo/eval.py --dry-run
   python demo/eval.py --only runtime --data demo/out/paper/results.json --output figures/runtime

The last command exports one figure with ``report.json`` and
``report-data.json``. Only that figure's required measurements are collected;
the data export includes all previously collected attempts from its commits.
See :doc:`data-format` for both JSON formats.

To replace that figure using new measurement arguments and the current cache:

.. code-block:: bash

   python demo/eval.py --only runtime --samples 5 --data demo/out/paper/results.json --output figures/runtime

The configuration changes the measurement identity. Matching data is reused;
missing data is collected. The figure and report bundle replace the previous
generated files in ``figures/runtime``. ``--force`` requests fresh observations
even when matching data exists.

Replay bundled data
-------------------

Keep ``report.json`` and ``report-data.json`` in the same folder. Replay uses
the current evaluation script with the exact run selections from that report:

.. code-block:: bash

   python demo/eval.py --render-only --report figures/runtime/report.json --output figures/replayed-runtime

To select one figure from a report containing several outputs, add
``--only runtime``. An equivalent way to identify the input bundle is:

.. code-block:: bash

   python demo/eval.py --render-only --data figures/runtime/report-data.json --only runtime --output figures/replayed-runtime

Replay reads only the bundled data. It never opens the normal cache, collects
measurements, or checks out code. The source bundle remains unchanged; a
different output directory is required. Missing data, unknown output IDs,
changed requirement names, or incompatible benchmark versions fail before
replacing outputs. There is no fallback to another cache.

Measurement arguments such as ``--samples`` or ``--commit`` do not change the
recorded selections during replay. To apply new measurement arguments or
selector functions, use a normal run instead. Figure callbacks always run
again, so changes to rendering code take effect in either mode.

The data keeps the collecting script's commit and the workload commits.
The new ``report.json`` records the current script's commit for the generated
figures, along with the actual replay arguments. Uncommitted changes in that
script's checkout produce a warning.

Shared options
--------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Behavior
   * - ``--output DIR``
     - Directory for generated files. Defaults to ``out`` unless the script
       supplies another default; the demo uses ``demo/out/paper``.
   * - ``--data FILE``
     - Measurement cache for normal runs; defaults to ``OUTPUT/results.json``.
       In replay, accepts only the bundle's ``report-data.json``.
   * - ``--only ID [ID ...]``
     - Select output IDs. Defaults to all registered outputs for a normal run,
       or all outputs recorded in the supplied report for replay.
   * - ``--render-only``
     - Replay a bundle. Requires ``--report`` or ``--data`` and a different
       output directory. Cannot be combined with ``--force`` or ``--dry-run``.
   * - ``--report FILE``
     - The ``report.json`` to replay. Requires ``--render-only``; data always
       comes from the sibling ``report-data.json``.
   * - ``--commit REF``
     - Default workload revision for treatments that omit ``commit``.
       Defaults to ``HEAD``. Affects collection only.
   * - ``--benchmarks NAME [NAME ...]``
     - Allowed benchmarks for collection. An error is raised if an output
       requires an excluded benchmark; it is not rendered with partial data.
   * - ``--jobs N``
     - Maximum simultaneous treatments marked ``parallel=True``. Defaults to
       CPU count; ``1`` runs sequentially. Unmarked treatments run alone.
   * - ``--timeout SECONDS``
     - Positive, finite deadline per command, overriding benchmark defaults.
       No limit by default unless the benchmark declares one. Increasing or
       removing a deadline permits retrying timed-out treatments.
   * - ``--force``
     - Collect fresh observations for the selected treatments, retaining
       previous attempts in the cache.
   * - ``--dry-run``
     - Print the collection plan without collecting or changing any files.

All parsed arguments and defaults are recorded. Script-specific arguments
affect measurement cache identity unless excluded through
``Registry(selection_arguments=...)``. ``--jobs`` also affects that identity.
Output and selection controls do not; timeout reuse follows the policy in
:doc:`python-guide`. CLI arguments are recorded configuration, so scripts
should declare measurement parameters in their parser rather than hide them
in globals or environment variables.
