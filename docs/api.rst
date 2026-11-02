Python API reference
====================

Registry
--------

A ``Registry`` declares collection callbacks and paper outputs. See
:doc:`python-guide` for examples and :doc:`data-format` for saved tables.

.. autoclass:: eval_live.Registry
   :members:

.. autofunction:: eval_live.select

.. autofunction:: eval_live.add_arguments

.. autofunction:: eval_live.warn

.. autofunction:: eval_live.error

Benchmark callbacks
-------------------

``Registry.benchmark`` accepts the configuration fields of ``Benchmark``.
Collection callbacks receive a ``RunContext`` and return named flat tables.

.. autoclass:: eval_live.Benchmark
   :members:

.. autoclass:: eval_live.RunContext
   :members:

.. autofunction:: eval_live.run_command

.. autoclass:: eval_live.ProcessResult
   :members:

.. autoclass:: eval_live.ProcessError
   :members:

Plans, results, and storage
---------------------------

.. autoclass:: eval_live.Plan
   :members:

.. autoclass:: eval_live.EvaluationResult
   :members:

.. autoclass:: eval_live.Store
   :members:

Browser assets
--------------

.. autofunction:: eval_live.css

.. autofunction:: eval_live.js

.. autofunction:: eval_live.pyodide_lib

A standalone browser script assigns its registry to ``eval_live.registry``.
``Registry.run`` generates the page and connects the same registered callbacks
to their saved sample selections automatically.

LaTeX helpers
-------------

.. automodule:: eval_live.latex
   :members:
