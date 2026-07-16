"""Reading the configuration, and refusing to guess when it is wrong."""

from pathlib import Path

import pytest

from mailrun.config import default_config_path, load_config
from mailrun.errors import ConfigError, UnknownAccountError, UnknownProviderError

TWO_ACCOUNT_CONFIG = """
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "me@naver.com"

[accounts.gmail]
provider = "gmail"
username = "me@gmail.com"

[contacts]
lead = "lead-naver"
lead-naver = "lead@naver.com"
lead-gmail = "lead@gmail.com"
team = ["me@naver.com", "lead"]
"""


def write_config(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestAccounts:
    def test_accounts_are_read_with_their_providers(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert set(config.account_by_name) == {"naver", "gmail"}
        assert config.account_by_name["naver"].username == "me@naver.com"
        assert config.account_by_name["naver"].provider.smtp_host == "smtp.naver.com"
        assert config.account_by_name["gmail"].provider.smtp_host == "smtp.gmail.com"

    def test_account_defaults_to_the_named_default(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.resolve_account().name == "naver"

    def test_account_can_be_asked_for_by_name(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.resolve_account("gmail").username == "me@gmail.com"

    def test_unknown_account_names_the_configured_ones(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        with pytest.raises(UnknownAccountError) as caught:
            config.resolve_account("outlook")
        assert "gmail" in str(caught.value)

    def test_lone_account_needs_no_default_declared(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        assert load_config(path).resolve_account().name == "naver"

    def test_several_accounts_without_a_default_is_refused_rather_than_guessed(
        self, tmp_path
    ):
        config_without_default = TWO_ACCOUNT_CONFIG.replace(
            'default_account = "naver"', ""
        )
        with pytest.raises(ConfigError, match="no default_account"):
            load_config(write_config(tmp_path, config_without_default))

    def test_default_naming_a_missing_account_is_refused(self, tmp_path):
        path = write_config(
            tmp_path,
            'default_account = "outlook"\n'
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        with pytest.raises(ConfigError, match="not a\n?\\s*configured account"):
            load_config(path)

    def test_unknown_provider_is_refused_at_load_time(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.work]\nprovider = "hanmail"\nusername = "me@hanmail.net"\n',
        )
        with pytest.raises(UnknownProviderError, match="hanmail"):
            load_config(path)

    def test_account_missing_a_username_is_refused(self, tmp_path):
        path = write_config(tmp_path, '[accounts.naver]\nprovider = "naver"\n')
        with pytest.raises(ConfigError, match="username"):
            load_config(path)


class TestAddressBook:
    def test_string_contact_becomes_a_one_entry_alias(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.address_book["lead-naver"] == ("lead@naver.com",)

    def test_list_contact_becomes_a_group(self, tmp_path):
        config = load_config(write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.address_book["team"] == ("me@naver.com", "lead")

    def test_address_book_may_be_absent(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        assert load_config(path).address_book == {}

    def test_contact_of_the_wrong_shape_is_refused(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            "[contacts]\nlead = 42\n",
        )
        with pytest.raises(ConfigError, match="must be a string or a list"):
            load_config(path)

    def test_quoted_alias_may_contain_a_dot(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            '[contacts]\n"jane.doe" = "jane@example.com"\n',
        )
        config = load_config(path)
        assert config.address_book["jane.doe"] == ("jane@example.com",)

    def test_quoted_dotted_aliases_can_be_grouped(self, tmp_path):
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            "[contacts]\n"
            '"jane.doe" = "jane@example.com"\n'
            '"john.roe" = "john@example.com"\n'
            'team = ["jane.doe", "john.roe"]\n',
        )
        config = load_config(path)
        assert config.address_book["team"] == ("jane.doe", "john.roe")

    def test_unquoted_dotted_alias_explains_the_toml_nesting_trap(self, tmp_path):
        # TOML reads `jane.doe = "..."` as a table `jane` holding `doe`, so the
        # reader is told about an alias named "jane" they never wrote. The error
        # has to name the cause or it sends them hunting.
        path = write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            "[contacts]\njane.doe = \"jane@example.com\"\n",
        )
        with pytest.raises(ConfigError) as caught:
            load_config(path)
        message = str(caught.value)
        assert "unquoted" in message
        assert "jane.doe" in message
        assert '"jane.doe" = ' in message  # shows the corrected line


class TestFileItself:
    def test_missing_file_says_where_it_looked_and_what_to_write(self, tmp_path):
        with pytest.raises(ConfigError) as caught:
            load_config(tmp_path / "absent.toml")
        message = str(caught.value)
        assert "absent.toml" in message
        assert "accounts" in message

    def test_malformed_toml_is_reported_as_such(self, tmp_path):
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(write_config(tmp_path, "this is not toml ["))

    def test_file_without_accounts_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="no accounts"):
            load_config(write_config(tmp_path, 'default_account = "naver"\n'))


class TestDefaultLocation:
    def test_config_lives_under_the_xdg_directory_not_the_project(self, monkeypatch):
        monkeypatch.delenv("MAILRUN_CONFIG", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: Path("/home/tester"))
        assert default_config_path() == Path("/home/tester/.config/mailrun/config.toml")

    def test_xdg_config_home_is_honoured(self, monkeypatch):
        monkeypatch.delenv("MAILRUN_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_config_path()) == "/elsewhere/config/mailrun/config.toml"

    def test_explicit_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MAILRUN_CONFIG", "/tmp/other.toml")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_config_path()) == "/tmp/other.toml"
