Data format and provenance
==========================

The data/cache file is one JSON object mapping names to lists of row objects.
Every stored cell is a string, finite number, boolean, or null. Metadata uses
reserved ``eval_`` names. User tables reference observations through ``run_id``.
Do not put styled cells, lists, dictionaries, or serialized configuration
objects in stored cells. Styled cells are allowed in derived presentation tables.

Schema version 3
----------------

======================= ======================================================
Table                   Contents
======================= ======================================================
``eval_metadata``       Singleton schema and writer versions.
``eval_invocations``    Invocation identity, timestamps/status, script path,
                        script Git revision/dirty state, Python/platform, cwd,
                        and whether collection completed.
``eval_argv``           Invocation ID, original token position and token.
``eval_arguments``      All resolved invocation options, with scalar types and
                        list positions, including defaults and empty lists.
``eval_configs``        Request, measurement-argument, and build parameters,
                        grouped by config ID and scope.
``eval_environments``   Environment ID and named platform/Python/tool settings.
``eval_runs``           Attempt ID, collecting invocation, benchmark, resolved
                        commit/ref, cache identity, configuration/environment,
                        data version, status, timing, and error.
``eval_processes``      Process ID, run, cwd, duration, status, exit code, logs.
``eval_commands``       Process/run ID and ordered command argument values.
User tables             Scalar measurement rows with library-supplied run ID.
======================= ======================================================

Parameter encoding distinguishes scalar, list, empty-list, and empty-mapping
kinds. Each value has an explicit type, so ``1``, ``1.0``, ``"1"``, and
``true`` remain distinct. List items have ordered indices. Empty lists/maps
use a marker row with index -1 and null value. Paths in CLI arguments normalize
to strings. A configuration's three scopes are ``request``, ``arguments``, and
``build``.

Rows and outcomes
-----------------

``run_id`` links measurement rows to a treatment's configuration and commit.
Repeated or forced evaluations retain earlier attempts. The cache contains no
per-output selections; those belong to the generated ``report.json``.

Every requested treatment has rows in every table declared by ``error_rows``
before collection begins. This invariant applies to every attempt.
``eval_runs.status`` is one of ``pending``, ``running``,
``success``, ``timed-out``, ``failure``, or ``interrupted``. Error rows satisfy
output requirements just as measured rows do. Unfinished attempts from a
stopped writer become ``interrupted`` when the next writer starts.

``eval_runs.data_version`` is the benchmark's declared table/protocol version.
Changing it creates a new cache identity; saved attempts keep their version.
It is independent of the file's ``schema_version``.

``eval_runs.timeout`` is the per-command deadline in seconds, or null for no
limit. ``seconds`` is elapsed attempt time, including preparation and builds.
Each attempt keeps the deadline it used. The :doc:`python-guide` describes
cache reuse when the deadline changes.

``eval_invocations.collection_complete`` records that all requested treatments
finished, including those represented by timeout or failure rows. Rendering
may still report errors. Render-only invocations are recorded in ``report.json``
and do not modify the measurement cache.

Computed warning/error tables are rendering outputs and are never added to
this file. Execution diagnostics in ``eval_runs`` and ``eval_processes`` are
stored facts that figures can use when computing those messages.

Persistence
-----------

``Store`` validates scalar cells, schema version, identities, parameter
encoding, provenance links, and cache fingerprints before collection.
Incompatible data raises an error; it is never silently deleted or converted.
For an older schema, use a new ``--data`` path for collection and the original
eval-live version to read the older file.
The current repository identity is the canonical common Git-directory path,
so another clone has a separate collection identity. Report selections remain
portable for render-only use.

Writes are atomic: readers see the complete previous or new snapshot. One
writer holds ``results.json.lock``; concurrent writers fail immediately. The
lock file can remain after exit, but the lock is released when the writer
stops. Benchmark results are stored only in the JSON file.

Report bundle
-------------

``report-data.json`` uses the cache schema above and is accepted by ``Store``
and ``--data``. It contains every cached attempt for the repository/commit pairs
referenced by the report, including other configurations, treatments, data
versions, environments, failures, and forced attempts. Associated measurement
rows, command diagnostics, and collection provenance are included. Empty tables
retain their names. Other commits are omitted, and exporting collects no extra
data. The HTML embeds the same tables.

``report.json`` is a separate generation record, not a flat measurement table:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Contents
   * - ``schema_version``
     - Report format version, currently 1.
   * - ``data``
     - Sibling data filename, ``report-data.json``.
   * - ``invocation``
     - Generation ID, timestamps, script path, script Git commit and dirty
       state, Python/platform, cwd, and status. A replay also records its
       source report's invocation ID.
   * - ``arguments``
     - All resolved CLI arguments, including defaults and lists.
   * - ``argv``
     - Original command-line tokens, in order.
   * - ``selections``
     - Output ID → requirement name → ordered list of run IDs.

Empty requirements have an empty list. Selections fix the exact attempts used
for rendering even when the bundled data includes several commits or attempts
for the same configuration. They are also used in the interactive HTML.
Callbacks receive temporary ``eval_requirements`` and ``eval_selections`` tables
for :func:`eval_live.select`; these are not persistent cache tables.

``eval_invocations.script_commit`` identifies the code that collected the data;
``eval_runs.commit`` identifies workload sources. The separate
``report.json`` → ``invocation.script_commit`` identifies the code that rendered
the figures. Replay retains the collection commits and records the current
rendering commit. ``script_dirty`` is true if the script's repository
has tracked or untracked changes. A dirty checkout produces a CLI warning. If
the script is outside Git, its revision and dirty state are null. No standalone
copy of the script is exported. The HTML still embeds Python rendering callbacks
for interactive use.

Keep both JSON files with the paper outputs. ``--render-only --report PATH``
reads only the sibling data file. ``--data PATH/report-data.json`` is an
alternative spelling that finds ``report.json`` alongside it; a normal cache
cannot override the bundle. Replay leaves both inputs unchanged, requires a
different output directory, and uses the current rendering code and dependencies
without checking out any revision. See :doc:`python-guide` for commands and
constraints.
