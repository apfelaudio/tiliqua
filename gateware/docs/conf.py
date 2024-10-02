import os, sys
sys.path.insert(0, os.path.abspath("."))

import time
from importlib.metadata import version as package_version


project = "Tiliqua Project"
copyright = time.strftime("2024—%Y, S. Holzapfel and Tiliqua contributors")

extensions = [
    "sphinx.ext.napoleon",
	"sphinx.ext.intersphinx",
	"sphinx.ext.doctest",
    "sphinx.ext.todo",
    "sphinx.ext.autodoc",
    "sphinx_rtd_theme",
    "sphinxcontrib.platformpicker",
]

with open(".gitignore") as f:
    exclude_patterns = [line.strip() for line in f.readlines()]

root_doc = "cover"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

todo_include_todos = True

autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True
}
autodoc_preserve_defaults = True
autodoc_inherit_docstrings = False

# Tiliqua mostly does not include typehints, and showing them in some places but not others is
# worse than not showing them at all.
autodoc_typehints = "none"

napoleon_google_docstring = False
napoleon_numpy_docstring = True
napoleon_use_ivar = True
napoleon_include_init_with_doc = True
napoleon_include_special_with_doc = True
napoleon_custom_sections = [
    ("Attributes", "params_style"), # by default displays as "Variables", which is confusing
    ("Members", "params_style"), # `lib.wiring` signature members
    "Platform overrides"
]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_logo = "_static/logo.jpg"

rst_prolog = """
.. role:: py(code)
   :language: python
"""

linkcheck_ignore = [
    r"^http://127\.0\.0\.1:8000$",
]

linkcheck_anchors_ignore_for_url = [
    r"^https://matrix\.to/",
    r"^https://web\.libera\.chat/",
    # React page with README content included as a JSON payload.
    r"^https://github\.com/[^/]+/[^/]+/$",
]
