Python API reference
====================

Evaluation and planning
-----------------------

.. autoclass:: eval_live.Evaluation
   :members:

.. autoclass:: eval_live.Plan
   :members:

.. autoclass:: eval_live.EvaluationResult
   :members:

.. autoclass:: eval_live.CollectionError
   :members:

.. autofunction:: eval_live.add_arguments

Benchmarks and requirements
---------------------------

.. autoclass:: eval_live.Benchmark
   :members:

.. autoclass:: eval_live.RunRequest
   :members:

.. autoclass:: eval_live.RunContext
   :members:

.. autofunction:: eval_live.run_command

.. autoclass:: eval_live.ProcessResult
   :members:

.. autoclass:: eval_live.ProcessError
   :members:

Artifacts
---------

.. autoclass:: eval_live.artifacts.Artifact
   :members:

.. autoclass:: eval_live.Figure
   :members:

.. autoclass:: eval_live.Table
   :members:

.. autoclass:: eval_live.Macros
   :members:

.. autoclass:: eval_live.ArtifactView
   :members:

Storage
-------

.. autoclass:: eval_live.Store
   :members:

Legacy Registry and browser assets
----------------------------------

.. autoclass:: eval_live.Registry
   :members:

.. autofunction:: eval_live.css

.. autofunction:: eval_live.js

.. autofunction:: eval_live.pyodide_lib

The legacy browser script assigns its registry to ``eval_live.registry``.
The new HTML exporter builds that registry from artifact definitions.

LaTeX helpers
-------------

.. automodule:: eval_live.latex
   :members:
