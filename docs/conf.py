"""Build the Python reference from the installed package's docstrings."""

project = "eval-live"
extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.doctest"]
html_theme = "alabaster"
exclude_patterns = ["_build"]
autodoc_member_order = "bysource"
napoleon_numpy_docstring = True
