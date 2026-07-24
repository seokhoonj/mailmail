"""What reaches a user, as opposed to what sits in the repo.

The two are not the same thing, and the gap is silent. This package spent its
whole life fully annotated and `mypy --strict` clean while every one of those
hints was invisible to everybody who installed it: PEP 561 says a checker must
ignore an installed package's inline types unless a `py.typed` marker ships
beside them, and there was none. So `to` being required -- caught by the checker
at author time -- was a promise that held only inside this repo.

The wheel is where that gets settled, and CI opens one (see check.yml: it
installs the built package into a clean environment and runs a user's mypy over
user code). This file only guards the near end: the marker is in the source
tree, so deleting it fails here in a second rather than in CI in a minute.
"""

import importlib.metadata
from pathlib import Path

import mailmail


def test_the_distribution_and_runtime_versions_agree():
    """The version `pip show` reports and the one `mailmail.__version__` returns
    are the same number.

    The version is written twice on purpose -- once in `pyproject.toml`, which
    the build reads into the distribution metadata, and once as `__version__`, so
    a caller can read it without `importlib.metadata`. The Python packaging guide
    sanctions that duplication and asks for exactly this in return: a test that
    the two do not drift.

    It bites on a fresh install -- what CI does on every run, and what a user's
    `pip install` does: a bumped `pyproject.toml` that forgot `__version__` (or
    the reverse) fails here, because `importlib.metadata.version` reads the
    just-installed metadata. Under an editable install the metadata is frozen at
    install time, so a later edit to one file can slip past until the next
    reinstall; the run that gates a release is the fresh one, and there it holds.
    """
    assert importlib.metadata.version("mailmail") == mailmail.__version__


def test_the_py_typed_marker_sits_beside_the_code():
    """PEP 561's marker, found the way a checker finds it: next to `__init__`.

    Located through `mailmail.__file__` rather than a path relative to this test,
    so it follows the package to wherever it was installed instead of asserting
    something about the repo layout.
    """
    marker = Path(mailmail.__file__).parent / "py.typed"
    assert marker.exists(), (
        "py.typed is missing, so every type hint in this package is invisible to "
        "anyone who installs it -- their checker will not read a single one"
    )


def test_the_marker_is_empty():
    """PEP 561 gives the file no contents to have. An empty marker is the whole
    protocol; anything written in it would be a note to nobody."""
    marker = Path(mailmail.__file__).parent / "py.typed"
    assert marker.read_text() == ""
