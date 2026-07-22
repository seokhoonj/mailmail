"""Diff the package's blocked-extension list against Google's published page.

The size limit is asked of the server on every send, so it cannot go stale. The
blocked list cannot be asked of anything -- it is a snapshot of what Google
published the day someone read it, and Google can change it without telling us.
This is how that drift gets noticed instead of discovered by a bounce.

Not in the pytest suite: it needs the network, so it would make the suite
non-deterministic and dependent on a page staying up. Run it when you wonder.

    .venv/bin/python scripts/check_blocked_list.py

Exits non-zero if the lists differ, so a scheduled job can call it.
"""

import re
import sys
import urllib.error
import urllib.request

from mailmail.provider import EXECUTABLE_EXTENSIONS

SOURCE = "https://support.google.com/mail/answer/6590"

# The page lists the types as a run of comma-separated dotted extensions. Pulling
# every `.xyz` on the page would also catch prose ("like .zip or .tgz files"), so
# this looks for the run itself: three or more in a row, comma-separated.
#
# An extension must contain a letter. Without that, the version numbers scattered
# through the page ("5.7.0") parse as a run and report `.0`, `.2`, `.25` as types
# Google blocks and we do not -- which is what the first version of this did. The
# lookahead keeps `.7z` (a letter, just not first) while dropping `.25`.
_EXTENSION = r"\.(?=[a-z0-9_]*[a-z])[a-z0-9_]{1,12}"
_RUN = re.compile(rf"(?:{_EXTENSION},\s*){{2,}}{_EXTENSION}")
_ONE = re.compile(_EXTENSION)


def published_extensions(html: str) -> set[str]:
    """Every extension named in the page's blocked-types run."""
    text = re.sub(r"<[^>]+>", " ", html)
    found: set[str] = set()
    for run in _RUN.findall(text):
        found.update(_ONE.findall(run))
    return found


def main() -> int:
    try:
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "mailmail"})
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as err:
        print(f"could not read {SOURCE}: {err}")
        return 2

    published = published_extensions(html)
    if not published:
        # The page moved or changed shape. Say so rather than report a clean diff
        # against nothing, which would read as "no drift".
        print(f"found no extension list at {SOURCE} -- the page's shape changed")
        return 2

    ours = set(EXECUTABLE_EXTENSIONS)
    missing = published - ours
    extra = ours - published

    print(f"published: {len(published)}   ours: {len(ours)}")
    if missing:
        print("\nGoogle blocks these and we do not -- we would pass a bounce:")
        for ext in sorted(missing):
            print(f"  {ext}")
    if extra:
        print("\nwe block these and the page no longer lists them:")
        for ext in sorted(extra):
            print(f"  {ext}")
    if not missing and not extra:
        print("\nidentical -- the snapshot is still current")
        return 0
    print("\nUpdate EXECUTABLE_EXTENSIONS in src/mailmail/provider.py if the page")
    print("is right.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
