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

from pathlib import Path

import mailrun


def test_the_py_typed_marker_sits_beside_the_code():
    """PEP 561's marker, found the way a checker finds it: next to `__init__`.

    Located through `mailrun.__file__` rather than a path relative to this test,
    so it follows the package to wherever it was installed instead of asserting
    something about the repo layout.
    """
    marker = Path(mailrun.__file__).parent / "py.typed"
    assert marker.exists(), (
        "py.typed is missing, so every type hint in this package is invisible to "
        "anyone who installs it -- their checker will not read a single one"
    )


def test_the_marker_is_empty():
    """PEP 561 gives the file no contents to have. An empty marker is the whole
    protocol; anything written in it would be a note to nobody."""
    marker = Path(mailrun.__file__).parent / "py.typed"
    assert marker.read_text() == ""
