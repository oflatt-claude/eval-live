Command-line reference
======================

:func:`eval_live.add_arguments` adds these options to an evaluation script's
``argparse`` parser. Run ``python demo/eval.py --help`` to see them alongside
script-specific parameters such as ``--samples`` and ``--sizes``. Spell option
names in full.

Run an evaluation
-----------------

With no arguments, the script generates all declared figures, tables, macros,
and HTML. It reuses matching measurements, collects missing treatments, and
opens the saved report in the default browser. ``--no-browser`` disables the
browser launch; an unavailable browser does not fail the evaluation.

.. code-block:: bash

   python demo/eval.py
   python demo/eval.py --only runtime --output figures/runtime
   python demo/eval.py --only runtime --samples 5 --output figures/runtime

The second command collects only the data needed by ``runtime`` and writes
that figure with its HTML report, ``results.json``, and ``reproduce.json``.
The third replaces it using five samples per configuration. Changing a
measurement parameter changes cache identity; matching observations are reused
and missing observations are collected. ``--force`` requests fresh observations.
See :doc:`data-format` for the exported JSON formats.

Each run prints its selected output IDs, cache location, output directory, and
collection/reuse counts. ``--dry-run`` shows the individual treatments, their
cache status, and which outputs require them, without writing files. An unknown
``--only`` ID produces an error listing the registered output IDs.

Cache and output paths
----------------------

The cache location is independent of ``--output``. It defaults to ``cache.json``
beside the script's declared default output directory, or to the ``cache=`` path
supplied to :func:`eval_live.add_arguments`. For the demo it is always
``demo/out/cache.json``, including when exporting to ``figures/runtime``.
Use ``--cache FILE`` to choose another cache. Relative paths are resolved from
the working directory.

The output directory is disposable: **all files**, including hidden and
unrecognized files, are cleared before collection or rendering starts. This
also applies when selecting one output with ``--only``. Use a separate output
directory to retain the full report. Subdirectories or symlinks cause an error
before any deletion; inspect and clear them manually. Validation failures and
``--dry-run`` leave the directory untouched. A failed or interrupted run leaves
no report, while collected measurements remain in the cache.

Caches, source files, and replay inputs must stay outside the output directory,
including its subdirectories. Paths that would erase the working directory,
home directory, a Git checkout root, or known source files are rejected. The
output directory itself cannot be a symlink.

Replay a saved report
---------------------

Pass the folder containing both ``reproduce.json`` and ``results.json``:

.. code-block:: bash

   python demo/eval.py --replay figures/runtime --output figures/replayed-runtime
   python demo/eval.py --replay demo/out/paper --only runtime --output figures/replayed-runtime

Replay uses the current rendering code with the exact run selections saved in
the report. It reads only the bundle, never the normal cache, and neither
collects measurements nor checks out code. The source folder remains unchanged;
a different output directory is required. Missing data, unknown output IDs,
changed requirement names, or incompatible benchmark versions fail before
replacing outputs.

Only ``--only``, ``--output``, and ``--no-browser`` may accompany ``--replay``.
Explicit collection options, including ``--cache``, ``--commit``, ``--jobs``,
``--timeout``, ``--force``, ``--dry-run``, and script-specific parameters such as
``--samples``, are rejected. To change measurements or requirements, run normally.
Figure callbacks run again in either mode.

The data retains the collecting script's commit and workload commits. The new
``reproduce.json`` records the current rendering script's commit and the replay
invocation. Uncommitted changes in that script's checkout produce a warning.

Options
-------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Option
     - Behavior
   * - ``--output DIR``
     - Report directory whose files are cleared before running. Defaults to
       ``out``; the demo uses ``demo/out/paper``. Does not move the cache.
   * - ``--only ID [ID ...]``
     - Select output IDs. Defaults to all registered outputs in a normal run,
       or all outputs saved in the source report during replay.
   * - ``--replay FOLDER``
     - Render from that folder's ``reproduce.json`` and ``results.json``.
   * - ``--no-browser``
     - Save the report without opening it in a browser.
   * - ``--cache FILE``
     - Writable JSON measurement cache. Defaults to the path chosen by the
       script when it creates its parser.
   * - ``--commit REF``
     - Default workload revision for treatments that omit ``commit``.
       Defaults to ``HEAD``.
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
     - Print the detailed collection plan without collecting or changing files.

All parsed arguments and defaults are recorded. Script-specific arguments
affect measurement cache identity unless excluded through
``Registry(selection_arguments=...)``. ``--jobs`` also affects that identity.
Output and selection controls do not affect measurement identity; timeout
reuse follows the policy in :doc:`python-guide`.
