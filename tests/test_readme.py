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

from mailrun import load_config, resolve_recipients
from mailrun.provider import GMAIL, NAVER

README = Path(__file__).parent.parent / "README.md"


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def config_block() -> str:
    """The TOML the reader is told to write into `~/.config/mailrun/config.toml`."""
    blocks: list[str] = re.findall(r"```toml\n(.*?)```", readme_text(), re.DOTALL)
    assert len(blocks) == 1, f"expected one toml block, found {len(blocks)}"
    return blocks[0]


@pytest.fixture
def readme_config(tmp_path, monkeypatch):
    """A loaded config, built from the README's own example."""
    path = tmp_path / "config.toml"
    path.write_text(config_block(), encoding="utf-8")
    monkeypatch.setenv("MAILRUN_CONFIG", str(path))
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

    def test_the_size_limits_match_the_providers(self):
        body = readme_text()
        for provider in (GMAIL, NAVER):
            assert f"{provider.max_message_bytes:,}" in body, (
                f"the README does not state {provider.smtp_host}'s real limit "
                f"({provider.max_message_bytes:,})"
            )

    def test_it_does_not_claim_a_test_count(self):
        # It cannot be right for longer than it takes to add a test.
        assert not re.search(r"\d+\s*개[,.]?\s*전부 오프라인", readme_text())

    def test_every_public_name_it_shows_exists(self):
        """A README that names a function the package does not export is a bug
        report from the future, filed by whoever pastes it."""
        import mailrun

        shown = set(re.findall(r"from mailrun import ([^\n]+)", readme_text()))
        names = {name.strip() for line in shown for name in line.split(",")}
        missing = [name for name in names if not hasattr(mailrun, name)]
        assert not missing, f"the README imports names that do not exist: {missing}"
