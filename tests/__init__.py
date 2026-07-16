"""Test package.

A package, not a loose directory, so `tests.*` is a name mypy can aim its
per-module settings at -- its module patterns match whole dotted components, so
a bare `test_*` glob never matches anything.
"""
