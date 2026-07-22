"""The README is executable documentation, so it gets executed.

It shipped a config example whose `[contacts]` defined `team = ["me", "lead"]`
without defining `me`. Anyone who pasted it got `UnknownContactError` on their
first send -- from the setup section, before ever reaching a mail server. It read
fine; six reviewers read past it; only running it found it.

So these tests lift the config block out of the README itself and resolve every
alias it defines. Not a copy of the block -- the block. A copy would drift, and
drift is the whole failure mode being guarded here.
"""

import re
import tomllib
from pathlib import Path

import pytest

from mailmail import load_config, resolve_recipients
from mailmail.attachment import _ENCODED_EXPANSION
from mailmail.provider import GMAIL, NAVER

README = Path(__file__).parent.parent / "README.md"


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def config_block() -> str:
    """The TOML the reader is told to write into `~/.config/mailmail/config.toml`.

    The README is bilingual, so the config example appears once per language. They
    must be identical -- a config that drifts between the English and Korean copies
    is the paste-and-fail this whole module guards against -- so assert that and
    return the one.
    """
    blocks: list[str] = re.findall(r"```toml\n(.*?)```", readme_text(), re.DOTALL)
    assert blocks, "expected a toml config block in the README"
    assert all(block == blocks[0] for block in blocks), (
        "the README's toml config blocks differ between languages"
    )
    return blocks[0]


@pytest.fixture
def readme_config(tmp_path, monkeypatch):
    """A loaded config, built from the README's own example."""
    path = tmp_path / "config.toml"
    path.write_text(config_block(), encoding="utf-8")
    monkeypatch.setenv("MAILMAIL_CONFIG", str(path))
    return load_config()


class TestTheConfigExampleWorks:
    def test_it_is_valid_toml(self):
        assert tomllib.loads(config_block())

    def test_it_loads(self, readme_config):
        assert readme_config.account_by_name

    def test_every_alias_it_defines_resolves(self, readme_config):
        """The P0. `team` referred to `me`, and `me` was not there."""
        book = readme_config.address_book
        for alias in book:
            resolved = resolve_recipients(alias, address_book=book)
            assert resolved, f"{alias!r} resolved to nothing"
            assert all("@" in address for address in resolved), (
                f"{alias!r} resolved to something that is not an address: {resolved}"
            )

    def test_it_does_not_teach_a_defaults_table(self):
        """The config example is what people paste. A `[defaults]` table in it
        would not merely be stale -- `load_config` refuses the whole file."""
        assert "[defaults]" not in config_block()

    def test_the_accounts_it_defines_resolve(self, readme_config):
        for name in readme_config.account_by_name:
            assert readme_config.resolve_account(name).provider

    def test_the_default_account_is_one_of_them(self, readme_config):
        assert readme_config.resolve_account(readme_config.default_account)


class TestTheFactsItStatesAreTheCodesFacts:
    """A number in prose is a copy, and copies drift.

    The test count did, twice: the README claimed 216 while the suite had 228,
    and the fix to 228 was stale again within the hour. That number is gone now
    -- these are the ones left, and they are pinned rather than trusted.
    """

    def test_the_size_limits_it_advises_match_the_providers(self):
        """Both language sections pin the ceiling a reader acts on.

        The README is bilingual: the English section states "N MB for <Provider>",
        the Korean "<PROVIDER> 약 NMB", and both have to agree with the code. It
        used to be the byte count -- 35,882,577 -- which nobody weighs a file
        against. The figure has to survive the same arithmetic the package does:
        the server's ceiling is post-encoding, and base64 adds about 37%.

        Truncated, not rounded. For a ceiling, 27 is safe advice at 27.8 MiB and
        28 is an over-promise.
        """
        body = readme_text()
        for provider in (GMAIL, NAVER):
            usable = provider.max_message_bytes / _ENCODED_EXPANSION
            advised = int(usable / 1024 / 1024)
            # Each section names the service the way it brands itself, which is
            # also what `provider.name` holds.
            english = f"{advised} MB for {provider.name.capitalize()}"
            korean = f"{provider.name.upper()} 약 {advised}MB"
            assert english in body, (
                f"the English README does not advise {provider.name}'s real "
                f"ceiling ({advised} MB of original files)"
            )
            assert korean in body.upper(), (
                f"the Korean README does not advise {provider.name}'s real "
                f"ceiling ({advised}MB of original files)"
            )

    def test_it_does_not_claim_a_test_count(self):
        # It cannot be right for longer than it takes to add a test.
        assert not re.search(r"\d+\s*개[,.]?\s*전부 오프라인", readme_text())

    def test_every_public_name_it_shows_exists(self):
        """A README that names a function the package does not export is a bug
        report from the future, filed by whoever pastes it."""
        import mailmail

        shown = set(re.findall(r"from mailmail import ([^\n]+)", readme_text()))
        names = {name.strip() for line in shown for name in line.split(",")}
        missing = [name for name in names if not hasattr(mailmail, name)]
        assert not missing, f"the README imports names that do not exist: {missing}"
