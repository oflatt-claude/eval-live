eval-live
=========

eval-live helps a paper evaluation use the same Python figure and table
definitions in the terminal, in paper artifacts, and in an interactive webpage.
It plans benchmark execution from each artifact's data requirements, caches
observations by commit and configuration, and records complete argument and
sample provenance in one flat JSON data file. One ``Registry`` holds both
benchmarks and outputs; graph/table callbacks receive dictionaries of named
tables.

.. toctree::
   :maxdepth: 2

   python-guide
   api
   data-format
   migration

Building the documentation
--------------------------

From the repository root:

.. code-block:: bash

   pip install -e '.[docs]'
   python -m sphinx -W --keep-going -b html docs docs/_build/html
   python -m sphinx -W -b doctest docs docs/_build/doctest

Open ``docs/_build/html/index.html``. API signatures and reference text come
from Python docstrings using `Sphinx autodoc
<https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html>`_. The
`Napoleon extension
<https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html>`_ reads
the existing NumPy-style parameter sections. Keep examples executable and
build with warnings treated as errors when changing the public API.
