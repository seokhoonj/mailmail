"""The skill file is a shipped artifact, so it gets checked like one.

Its frontmatter is YAML, and it was not: an unquoted `Trigger phrases:` inside
the description made the second `: ` read as a nested mapping, and the whole
block failed to parse. It shipped anyway, because the agent runtime's parser is
lenient enough not to care -- GitHub's is not, and neither is any other tool that
might read it. A file that only parses in one reader is broken.

Parsed with the standard library rather than PyYAML: the package has no runtime
dependencies and the test suite has no reason to add one for a header of two
keys. This reimplements only the sliver of YAML the frontmatter uses, and the
strictness is the point -- it must reject exactly what a real parser rejects.
"""

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / "skills" / "send-mail" / "SKILL.md"


def frontmatter_text() -> str:
    """The block between the opening and closing `---`."""
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "the file must open with a frontmatter fence"
    _open, block, _rest = text.split("---", 2)[0], *text.split("---", 2)[1:]
    return block


def frontmatter() -> dict[str, str]:
    """The frontmatter's keys, refusing anything a YAML parser would refuse.

    Only `key: value` lines with a quoted-or-colonless value are accepted, which
    is the whole grammar this header needs and exactly the rule the broken
    version violated.
    """
    parsed: dict[str, str] = {}
    for line in frontmatter_text().strip().splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-z-]+):\s*(".*"|[^"].*)', line)
        assert match, f"not a `key: value` line: {line!r}"
        key, value = match.group(1), match.group(2)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        else:
            assert ": " not in value, (
                f"{key} has an unquoted ': ' in its value, which YAML reads as a "
                f"nested mapping -- quote the whole value: {key}: \"{value}\""
            )
        parsed[key] = value
    return parsed


class TestFrontmatterIsRealYaml:
    def test_it_parses(self):
        assert frontmatter()

    def test_it_has_a_name_and_a_description(self):
        parsed = frontmatter()
        assert parsed["name"] == "send-mail"
        assert parsed["description"]

    def test_the_description_survives_its_own_colons(self):
        # "Trigger phrases:" is the exact thing that broke it.
        assert "Trigger phrases:" in frontmatter()["description"]

    def test_a_value_with_an_unquoted_colon_would_be_caught(self, tmp_path):
        # Guards the guard: if this check cannot fail, it is not checking.
        broken = 'name: x\ndescription: something. Trigger phrases: a, b\n'
        with pytest.raises(AssertionError, match="unquoted"):
            for line in broken.strip().splitlines():
                match = re.fullmatch(r'([a-z-]+):\s*(".*"|[^"].*)', line)
                assert match
                value = match.group(2)
                if not (value.startswith('"') and value.endswith('"')):
                    assert ": " not in value, "unquoted ': ' in value"


class TestTheSkillStaysAThinWrapper:
    """It must not grow a second copy of what the package knows.

    The skill already drifted once: it told users compressing an executable never
    helps, while .7z is not looked inside at all. One fact, two homes, and they
    were already different.
    """

    def test_it_does_not_restate_the_blocked_extension_list(self):
        # The list lives in provider.py. A copy here is a copy that goes stale.
        from mailrun.provider import EXECUTABLE_EXTENSIONS

        body = SKILL.read_text(encoding="utf-8")
        named = [ext for ext in EXECUTABLE_EXTENSIONS if f"`{ext}`" in body]
        assert not named, f"the skill names blocked extensions itself: {named}"

    def test_it_does_not_restate_the_size_limits(self):
        from mailrun.provider import GMAIL, NAVER

        body = SKILL.read_text(encoding="utf-8")
        for provider in (GMAIL, NAVER):
            assert str(provider.max_message_bytes) not in body
            assert f"{provider.max_message_bytes:,}" not in body

    def test_it_tells_the_reader_to_relay_the_exception_text(self):
        # The rule that replaced the restated facts.
        assert "str(err)" in SKILL.read_text(encoding="utf-8")


class TestItDoesNotAssumeItIsOnMyMachine:
    """The skill ships to whoever clones the repo, wherever they put it.

    It used to say the package lives at `~/Dropbox/mailrun` and to run
    `~/Dropbox/mailrun/.venv/bin/python`. That path is true on exactly one
    computer. The README tells everyone else to `git clone` and land wherever
    they are, so for them the skill pointed at nothing -- and it explained the
    config location by talking about Dropbox syncing, which is my arrangement,
    not theirs.

    The skill is symlinked out of the repo, so it can find the repo two levels up
    from itself. That works from any checkout, and there is nothing left to
    hardcode.
    """

    def test_it_names_no_absolute_path_from_one_persons_home(self):
        body = SKILL.read_text(encoding="utf-8")
        # `~/.config/...` is fine: that one really is the same for everybody.
        offenders = [
            line.strip()
            for line in body.splitlines()
            if "~/" in line and "~/.config" not in line and "~/.claude" not in line
        ]
        assert not offenders, (
            f"the skill points into a home directory layout only I have: {offenders}"
        )

    def test_it_names_no_particular_cloud_drive(self):
        body = SKILL.read_text(encoding="utf-8")
        for mine in ("Dropbox", "OneDrive", "iCloud", "Google Drive"):
            assert mine not in body, (
                f"the skill explains itself in terms of {mine}, which is where I "
                f"happen to keep this and says nothing about anyone else"
            )

    def test_it_finds_the_interpreter_instead_of_knowing_it(self):
        body = SKILL.read_text(encoding="utf-8")
        assert "os.path.realpath" in body, "the skill must resolve its own symlink"
        assert "NOT FOUND" in body, "and must have an answer for when that fails"

    def test_every_error_a_caller_must_act_on_is_in_the_table(self):
        """The one thing the skill is allowed to know: what to *do* per error.

        It may not restate the blocked list or the size limits -- those are the
        package's to state, and the tests above hold that line. But the recovery
        action is the skill's own knowledge, so the table is the one place a new
        error can go missing, silently, and the skill just shrugs at it.

        That is not hypothetical: adding `UnscannableArchiveError` left the table
        one error short the same day, and a deep zip would have reached the agent
        as a name it had never been told about.

        The base classes are exempt -- nothing raises a bare `AttachmentError`,
        and listing it would only tell the reader to catch a category it will
        never see.
        """
        from mailrun import errors

        abstract = {
            "MailrunError",
            "ConfigError",
            "ContactError",
            "AttachmentError",
            "CredentialsError",
        }
        body = SKILL.read_text(encoding="utf-8")
        missing = [
            name
            for name in errors.__all__
            if name not in abstract and f"`{name}`" not in body
        ]
        assert not missing, (
            f"the package raises errors the skill has no action for: {missing} -- "
            f"add a row to the exception table in {SKILL.name}"
        )
