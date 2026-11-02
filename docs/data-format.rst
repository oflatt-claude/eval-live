Data format and provenance
==========================

The data/cache file is one JSON object mapping names to lists of row objects.
Every stored cell is a string, finite number, boolean, or null. Metadata uses
reserved ``eval_`` names. User tables reference observations through ``run_id``.
Do not put styled cells, lists, dictionaries, or serialized configuration
objects in stored cells. Styled cells are allowed in derived presentation tables.

Schema version 1
----------------

======================= ======================================================
Table                   Contents
======================= ======================================================
``eval_metadata``       Singleton schema and writer versions.
``eval_invocations``    Invocation identity, timestamps/status, script identity,
                        script Git revision/dirty state, Python/platform, cwd.
``eval_argv``           Invocation ID, original token position and token.
``eval_arguments``      All resolved invocation options, with scalar types and
                        list positions, including defaults and empty lists.
``eval_configs``        Request, measurement-argument, and build parameters,
                        grouped by config ID and scope.
``eval_environments``   Environment ID and named platform/Python/tool settings.
``eval_runs``           Attempt ID, collecting invocation, benchmark, resolved
                        commit/ref, cache identity, configuration/environment,
                        data version, status, timing, binary hash, and error.
``eval_inputs``         Run ID, declared input path and SHA-256.
``eval_selections``     Rendering invocation, artifact, named requirement,
                        position, and selected run ID (fresh or reused).
``eval_requirements``   Rendering invocation, artifact, and requirement name,
                        including requirements with no selected observations.
``eval_processes``      Process ID, run, cwd, duration, status, exit code, logs.
``eval_commands``       Process/run ID and ordered command argument values.
User tables             Scalar measurement rows with library-supplied run ID.
======================= ======================================================

Parameter encoding distinguishes scalar, list, empty-list, and empty-mapping
kinds. Each value has an explicit type, so ``1``, ``1.0``, ``"1"``, and
``true`` remain distinct. List items have ordered indices. Empty lists/maps
use a marker row with index -1 and null value. Paths in CLI arguments normalize
to strings. A configuration's three scopes are ``request``, ``arguments``, and
``build``. No scope is hidden in a JSON-valued cell.

An observation's collecting invocation and a figure's rendering invocation may
be different: ``eval_selections`` records the exact reusable observations used.
Force runs retain previous attempts and selections. Failed/timed-out attempts
never satisfy a treatment's data requirement. A run left in ``running``
state by a stopped process becomes ``interrupted`` when the next writer starts.

The full treatment and CLI measurement parameters form the configuration key.
Repetitions are user-defined fields such as ``sample``; a CLI sample count is
also part of the configuration by default. There is no library sample counter.
Each attempt has a ``run_id`` linking its table rows to configuration and
commit provenance. Additional row keys such as phase belong to the paper's
table definitions; derived outputs can group rows and compute statistics.

``eval_runs.data_version`` records the collecting benchmark's declared
``version``. The version participates in the runner fingerprint: changing it
produces a new cache identity without deleting earlier data. It describes the
tables' format/meaning, independently of this JSON container schema version.
Earlier schema-1 observations may lack this field and retain their historical
``sample`` attempt index. They are not relabeled. The loader reconstructs
``eval_requirements`` from earlier nonempty selections when that table is absent.

``eval_runs.timeout`` is the per-command deadline used for that attempt. It
does not participate in cache identity: raising the deadline preserves successes
and permits retries of timed-out work. Equal/smaller deadlines keep cached
timeouts blocking; ordinary failures require ``--force``. For a smaller limit,
a success is reused only if its recorded ``seconds`` fits; this total attempt
duration includes setup/build work. Prior observations are never relabeled
with a later deadline. Early schema-1 files whose cache keys included timeout
remain readable and reusable without rewriting their observations.

Persistence
-----------

``Store`` validates scalar cells, schema version, identities, parameter
encoding, provenance links, and cache fingerprints before collection.
Incompatible data raises an error; it is never silently deleted or converted.
The current repository identity is the canonical common Git-directory path,
so another clone has a separate collection identity. Stored selections remain
portable for render-only use.

Writes use a temporary sibling, flush/fsync, and atomic replacement after each
completed observation and important status change. A single writer holds an OS
lock on ``results.json.lock``. The lock file can remain after exit; the OS lock
is released when the process stops. Concurrent writers fail immediately.
Readers see a complete previous or new snapshot. Build/worktree directories
and temporary files are disposable operational state, not additional stores.

This format favors small to medium paper evaluations. Each checkpoint rewrites
the JSON snapshot; database/streaming backends and parallel measurements are
not part of this version. Keep private inputs or process output out of shared
reports: the HTML contains the saved tables, including recorded diagnostics.
