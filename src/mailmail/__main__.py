"""Entry point for `python -m mailmail`.

The console script `mailmail` calls `main` directly; this lets the package be
run without the script being on `PATH`. Running `main` here is the one place the
package acts on import, and only when invoked as `__main__` -- `import mailmail`
never reaches it.
"""

from mailmail.cli import main

raise SystemExit(main())
