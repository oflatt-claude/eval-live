Python API reference
====================

Registry
--------

One registry holds benchmarks and outputs. Add ``requires=`` to the existing
``graph`` and ``table`` methods to select treatments with Python functions.
Without a selector an output uses the entire catalog. Callbacks continue to
receive ordinary data dictionaries. ``macros`` adds LaTeX definitions.

.. autoclass:: eval_live.Registry
   :members:

.. autofunction:: eval_live.select

.. autofunction:: eval_live.add_arguments

Benchmark callbacks
-------------------

``Registry.benchmark(name, fn, treatments=..., version=..., **options)`` accepts
the collection options listed on ``Benchmark``. Scripts register directly on their registry; they do
not need to construct benchmark objects separately.

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

.. autoclass:: eval_live.CollectionError
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
