Writing a paper evaluation
==========================

The complete example is ``demo/eval.py``. It creates a tiny local repository
with two commits, measures list/set membership, and defines a catalog of
figures, a table, and LaTeX macros. No compiler or external clone is needed.

.. code-block:: bash

   pip install -e '.[render,docs]'
   python demo/eval.py
   python demo/eval.py                         # all samples reused
   python demo/eval.py --dry-run               # show requirements and cache hits
   python demo/eval.py --samples 5             # collect only additional samples
   python demo/eval.py --only summary --sizes 500
   python demo/eval.py --seed 7                # new measurement configuration
   python demo/eval.py --force                 # fresh samples, retaining history

The default output directory is ``demo/out/paper``. It contains one
``results.json`` data/cache file, ``runtime.pdf``, ``history.pdf``,
``summary.tex``, ``macros.tex``, and ``index.html``. The ``.lock`` sibling is
operational state, not another data store. Open the HTML file directly. Graphs
and computed tables load Pyodide/matplotlib from a pinned CDN; raw tables work
without it. The browser only renders saved data and never runs benchmarks.

The generated fixture lives in ``demo/out/repository``. The first invocation
creates it; subsequent invocations use it. A first-ever ``--dry-run`` asks you
to initialize the fixture by running the demo first, so dry-run itself never
writes. Lookup timings exclude container construction and illustrate usage,
rather than constituting a rigorous performance study.

Declare benchmark observations
------------------------------

A ``Benchmark`` callback receives a ``RunContext`` and returns named flat
user tables. Each call represents one observation. The library adds
``run_id`` to every returned row and joins it to commit, configuration,
status, and environment metadata. Use scalar cells; repeated values belong
in separate rows. Tables beginning with ``eval_`` and the ``run_id`` column
are reserved.

.. code-block:: python

   def collect(context):
       import json
       import sys
       result = context.command([
           sys.executable, "worker.py", str(context.config["size"])
       ])
       return {"measurements": [json.loads(result.stdout)]}

   benchmark = eval_live.Benchmark(
       "worker", collect, version="1", inputs=["worker.py"], timeout=60,
   )

``context.directory`` is the requested Git revision in a temporary detached
worktree. ``context.config`` holds the request parameters; ``context.sample``
is a monotonically increasing attempt index for that identity.
``context.command`` runs without a shell and records the command, duration,
exit code, stdout, and stderr. Nonzero exits and timeouts raise
``ProcessError``. Its timeout is per subprocess; arbitrary Python callbacks
are not preempted. ``run_command`` exposes the same helper separately, without
automatic persistence.

Declare all measurement helpers in ``Benchmark(dependencies=[...])`` and
external input files/directories in ``inputs``. Relative inputs are read at
the target commit; absolute inputs are hashed from the local filesystem.
Declare toolchain/dependency versions with ``tools`` and environment variables
with ``environment``. If behavior depends on undeclared global values or
external services, explicitly version those dependencies or include them in
configuration. The library cannot infer their identity from callback source.

An optional ``build(context) -> Path`` prepares a binary. It receives
``Benchmark.build_config`` as its configuration; measurement callbacks receive
their own request configuration. Builds are shared across requests with the
same target, build code/configuration, inputs, and environment. The binary is
available as ``context.binary`` and its hash is saved. An entirely cached run
performs no build or worktree creation.

Describe outputs and their data requirements
--------------------------------------------------------

A ``RunRequest`` identifies one benchmark, Git ref, configuration, and required
sample count. Resolve arbitrary refs to full SHAs before looking up results.
Use ``HEAD`` for committed files, even when the current checkout is dirty.
``WORKTREE`` explicitly requests current working files; dirty working-tree
collection requires ``--allow-dirty`` and is never reusable.

.. doctest::

   >>> import eval_live
   >>> candidate = eval_live.RunRequest("worker", config={"size": 100}, samples=3)
   >>> candidate.commit
   'HEAD'
   >>> candidate.config["size"]
   100

Each artifact names one or more requirements. A requirement may contain one
request or a list, such as all sizes in a sweep. The planner deduplicates
identical requests, taking the maximum sample count needed across outputs.

.. code-block:: python

   def timings(view):
       return view.data["measurements"]

   table = eval_live.Table("timings", requires={"candidate": candidate}, render=timings)

Callbacks receive an ``ArtifactView``. ``view["candidate"]`` returns tables
restricted to the selected samples, including ``eval_runs`` and
``eval_configs`` for provenance. ``view.data`` is shorthand for an artifact
with exactly one named requirement. Rows are copied so callbacks do not alter
the stored snapshot.

.. doctest::

   >>> snapshot = {"measurements": [{"run_id": "sample-a", "seconds": 0.2}]}
   >>> view = eval_live.ArtifactView(snapshot, {"candidate": ["sample-a"]})
   >>> view.data["measurements"][0]["seconds"]
   0.2
   >>> view.data["measurements"].clear()
   >>> len(snapshot["measurements"])
   1

A ``Figure`` callback returns a matplotlib Figure. A ``Table`` callback returns
row dictionaries; styled presentation cells from the Registry API still work.
A ``Macros`` callback returns a mapping from LaTeX macro names (letters only)
to scalar values. Values are escaped; duplicate macro names across selected
artifacts are errors. Output filenames use the artifact ID, which must be
unique and contain only letters, digits, underscores, or hyphens.

For comparisons, declare separate names such as ``baseline`` and ``candidate``
and use ``view["baseline"]`` and ``view["candidate"]``. Each may refer to a
different configuration or commit. Import matplotlib inside graph callbacks
so table-only imports remain lightweight. Handle empty data after browser
filtering. Rendering callbacks must not collect measurements or require files
from the benchmark checkout.

Here is the demo's actual catalog and entry point:

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: main

CLI and cache behavior
----------------------

``add_arguments(parser)`` adds standard options. The script owns ordinary
``argparse`` parsing and uses ``args.samples`` and ``args.commit`` in requests.
``Evaluation.run(args=args, argv=tokens)`` stores every resolved option,
including defaults, and the exact original argument token sequence.

All script-specific options affect cache identity by default. Declare
selection-only options with ``selection_arguments``. For example, the demo
excludes the whole ``sizes`` list because each request already contains its
individual size. This lets a subset reuse measurements from a full sweep.
Output location, artifact selection, sample count, and other standard control
options do not affect individual observation identity. All are still saved.
A shared data file can be used with another artifact directory:

.. code-block:: bash

   python demo/eval.py --only summary --data demo/out/paper/results.json --output demo/out/summary

Cache identity includes repository identity, full SHA, measurement callback
and declared helper code, explicit protocol version, configuration, declared
input hashes, and environment/tool metadata. Changing plot code or
labels does not invalidate observations. Sample count is a minimum: raising
three to five collects two more. ``--force`` appends fresh attempts without
deleting old results. Exact selected sample IDs are stored for every artifact.

``--only`` selects artifact IDs before collection. ``--benchmarks`` restricts
allowed benchmark names; it errors if that would remove a required dependency.
``--dry-run`` prints cached/missing work and its reasons without writes, builds,
or measured commands. Collection is sequential. Failed attempts stop further
samples for that request, while independent requests continue. Failures remain
in the data file and raise ``CollectionError`` after complete independent
outputs are rendered. Interrupted attempts are recorded and can be retried;
completed observations survive restarts.

Timeouts and retries
--------------------

Set a benchmark's per-command deadline with ``Benchmark(timeout=60)``. The
shared ``--timeout`` option overrides it for all selected benchmarks, including
build commands. Timeouts kill the command's process group on POSIX and record
the attempt as ``timed-out`` with its stdout/stderr. Arbitrary Python callbacks
are not preempted; run bounded work through ``context.command``.

.. code-block:: bash

   python demo/eval.py --timeout 10
   python demo/eval.py --timeout 60  # reuse successes; retry timed-out work

Timeout is a retry bound, separate from measurement identity. Increasing it
keeps successful observations and makes timed-out requests eligible to collect
their missing samples. The newest applicable attempts satisfy the requested
sample count; older successes can fill it after a timeout becomes inapplicable.
An equal or smaller limit leaves cached timeouts blocked. Ordinary failures
also block automatic retries; ``--force`` collects fresh attempts regardless
of the cache. Changing the benchmark's default timeout follows the same rule
as changing the CLI option.

Successes are reusable at an equal or larger deadline. For a smaller deadline,
the saved total attempt duration must fit. That duration includes setup and
build time, so this check is conservative. Each attempt retains its original
deadline, status, and duration; the new invocation records the requested limit
separately. A dry-run displays the effective deadline and any cached failures
blocking collection. If timeout changes the algorithm itself, also put that
algorithm parameter in the request configuration.

Re-render a saved evaluation
--------------------------------------------

.. code-block:: bash

   python demo/eval.py --render-only
   python demo/eval.py --render-only --invocation SAVED_ID

Render-only uses the latest successful invocation by default, or the explicit
invocation's selected sample IDs. It does not resolve Git refs, inspect inputs,
build, or collect, and works when the benchmark repository has been removed.
The script defining callbacks and required rendering dependencies must still
be available. A missing artifact/requirement in that saved selection is an
error; use ``--only`` or choose an invocation containing it. Render-only records
its own arguments and links to the source invocation.

Generated files are replaced individually; unrelated old files in an output
directory are left alone. Use a separate output directory for subset reports.
An invocation with incomplete requirements is unsuccessful and may leave
previously generated files in place; inspect its error/status before using
outputs. The JSON file is validated and atomically checkpointed independently
of artifact generation.

Interactive callbacks
---------------------

The library embeds the same artifact callbacks and data in HTML. By default,
callbacks must be named top-level functions from one source file. Set
``browser_source=Path(__file__)`` explicitly, or provide a Python source string
with those functions. The source is imported as a module: a normal
``if __name__ == '__main__'`` guard keeps collection local. Put imports of
execution-only APIs inside the entry point; the browser facade exposes
``Registry``, ``RunRequest``, ``ArtifactView``, ``Figure``, ``Table``, and
``Macros``. Refer to the demo's ``import eval_live`` pattern.

A table's optional ``filter_source(rows, view)`` returns the run IDs matching
its visible computed rows. Only IDs in its declared requirements may be
returned. The viewer uses those IDs to narrow user tables, other computed
outputs, and graphs. Metadata remains available for reconstructing views.

.. literalinclude:: ../demo/eval.py
   :language: python
   :pyobject: narrow_summary

The browser runtime and its packages come from the `Pyodide CDN
<https://pyodide.org/en/stable/usage/quickstart.html#setup>`_. These are needed
for interactive Python outputs; the page embeds the raw data and the viewer's
JavaScript/CSS. HTML generation escapes data embedded in script elements.
