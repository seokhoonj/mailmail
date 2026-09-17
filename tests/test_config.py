"""Reading the configuration, and refusing to guess when it is wrong."""

from pathlib import Path

import pytest

from mailmail.config import (
    account_for,
    add_account,
    add_contact,
    add_group,
    config_dir,
    default_config_path,
    load_config,
)
from mailmail.errors import (
    ConfigError,
    InvalidAddressError,
    UnknownAccountError,
    UnknownProviderError,
)

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


def _write_config(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestAccounts:
    def test_accounts_are_read_with_their_providers(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert set(config.account_by_handle) == {"naver", "gmail"}
        assert config.account_by_handle["naver"].email == "me@naver.com"
        assert config.account_by_handle["naver"].provider.smtp_host == "smtp.naver.com"
        assert config.account_by_handle["gmail"].provider.smtp_host == "smtp.gmail.com"

    def test_account_defaults_to_the_named_default(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.resolve_account().handle == "naver"

    def test_account_can_be_asked_for_by_name(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.resolve_account("gmail").email == "me@gmail.com"

    def test_unknown_account_names_the_configured_ones(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        with pytest.raises(UnknownAccountError) as caught:
            config.resolve_account("outlook")
        assert "gmail" in str(caught.value)

    def test_lone_account_needs_no_default_declared(self, tmp_path):
        path = _write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        assert load_config(path).resolve_account().handle == "naver"

    def test_several_accounts_without_a_default_is_refused_rather_than_guessed(
        self, tmp_path
    ):
        config_without_default = TWO_ACCOUNT_CONFIG.replace(
            'default_account = "naver"', ""
        )
        with pytest.raises(ConfigError, match="no default_account"):
            load_config(_write_config(tmp_path, config_without_default))

    def test_default_naming_a_missing_account_is_refused(self, tmp_path):
        path = _write_config(
            tmp_path,
            'default_account = "outlook"\n'
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        with pytest.raises(ConfigError, match="not a\n?\\s*configured account"):
            load_config(path)

    def test_unknown_provider_is_refused_at_load_time(self, tmp_path):
        path = _write_config(
            tmp_path,
            '[accounts.work]\nprovider = "hanmail"\nusername = "me@hanmail.net"\n',
        )
        with pytest.raises(UnknownProviderError, match="hanmail"):
            load_config(path)

    def test_account_missing_a_username_is_refused(self, tmp_path):
        path = _write_config(tmp_path, '[accounts.naver]\nprovider = "naver"\n')
        with pytest.raises(ConfigError, match="username"):
            load_config(path)


NEW_FORMAT_CONFIG = """
default_account = "personal"

[[accounts]]
email = "me@naver.com"
alias = "personal"

[[accounts]]
email = "me@gmail.com"
alias = "work"
"""


class TestNewFormatAccounts:
    """The current `[[accounts]]` shape: an address, an optional alias, no provider."""

    def test_loads_array_of_tables_with_inferred_providers(self, tmp_path):
        config = load_config(_write_config(tmp_path, NEW_FORMAT_CONFIG))
        assert set(config.account_by_handle) == {"personal", "work"}
        personal = config.account_by_handle["personal"]
        assert personal.email == "me@naver.com"
        assert personal.provider.smtp_host == "smtp.naver.com"
        assert config.account_by_handle["work"].provider.smtp_host == "smtp.gmail.com"

    def test_an_account_without_an_alias_is_keyed_by_its_address(self, tmp_path):
        config = load_config(
            _write_config(tmp_path, '[[accounts]]\nemail = "solo@gmail.com"\n')
        )
        assert set(config.account_by_handle) == {"solo@gmail.com"}
        assert config.resolve_account().email == "solo@gmail.com"

    def test_an_unsupported_domain_is_refused(self, tmp_path):
        with pytest.raises(UnknownProviderError, match="unsupported email domain"):
            load_config(
                _write_config(tmp_path, '[[accounts]]\nemail = "me@hanmail.net"\n')
            )

    def test_an_explicit_provider_overrides_domain_inference(self, tmp_path):
        # A known provider on an off-list domain -- a Workspace address on a custom
        # domain. The escape hatch keeps the writer's inferred-by-default form usable.
        config = load_config(
            _write_config(
                tmp_path, '[[accounts]]\nemail = "me@company.com"\nprovider = "gmail"\n'
            )
        )
        assert config.resolve_account().provider.smtp_host == "smtp.gmail.com"

    def test_two_accounts_sharing_a_handle_are_refused(self, tmp_path):
        toml = (
            'default_account = "me"\n'
            '[[accounts]]\nemail = "a@naver.com"\nalias = "me"\n'
            '[[accounts]]\nemail = "b@gmail.com"\nalias = "me"\n'
        )
        with pytest.raises(ConfigError, match="share the handle"):
            load_config(_write_config(tmp_path, toml))

    def test_handles_that_fold_to_one_password_env_var_are_refused(self, tmp_path):
        # `me-naver` and `me.naver` are distinct handles but both fold to
        # MAILMAIL_PASSWORD_ME_NAVER, so an exported password could not say which
        # account it is for -- the disclosure the per-account name exists to prevent.
        toml = (
            'default_account = "me-naver"\n'
            '[[accounts]]\nemail = "a@naver.com"\nalias = "me-naver"\n'
            '[[accounts]]\nemail = "b@naver.com"\nalias = "me.naver"\n'
        )
        with pytest.raises(ConfigError, match="fold to the password"):
            load_config(_write_config(tmp_path, toml))

    def test_an_account_missing_an_email_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="email"):
            load_config(_write_config(tmp_path, '[[accounts]]\nalias = "x"\n'))

    def test_default_account_of_the_wrong_type_is_refused_not_crashed(self, tmp_path):
        # A list value reaching `x not in dict` would raise TypeError (unhashable),
        # escaping send()'s documented catch surface; it must be a ConfigError.
        toml = (
            'default_account = ["x"]\n'
            '[[accounts]]\nemail = "me@naver.com"\nalias = "me"\n'
        )
        with pytest.raises(ConfigError, match="default_account must be a string"):
            load_config(_write_config(tmp_path, toml))


class TestWriting:
    """The setup commands' engine: build, then persist, an account or address-book
    entry, without disturbing the rest of the file."""

    def test_account_for_infers_the_provider(self):
        account = account_for("me@naver.com", alias="personal")
        assert (account.email, account.alias, account.handle) == (
            "me@naver.com",
            "personal",
            "personal",
        )
        assert account.provider.smtp_host == "smtp.naver.com"

    def test_account_for_rejects_a_malformed_address(self):
        with pytest.raises(InvalidAddressError):
            account_for("not-an-email")

    def test_account_for_rejects_an_address_shaped_alias(self):
        with pytest.raises(InvalidAddressError, match="plain handle"):
            account_for("me@naver.com", alias="me@elsewhere")

    def test_add_contact_rejects_a_toml_hostile_address(self, tmp_path):
        # A bracket in an address never belongs there and would be a hostile value; it
        # is refused at entry, so nothing is written to feed the config writer.
        with pytest.raises(InvalidAddressError):
            add_contact("foo", "a[b@x.com", path=tmp_path / "config.toml")

    def test_add_account_round_trips_and_seeds_the_default(self, tmp_path):
        path = tmp_path / "config.toml"
        add_account(account_for("me@naver.com", alias="personal"), path=path)
        config = load_config(path)
        assert config.resolve_account("personal").email == "me@naver.com"
        assert config.default_account == "personal"  # the first account is the default

    def test_add_contact_and_group_round_trip(self, tmp_path):
        path = tmp_path / "config.toml"
        add_account(account_for("me@naver.com", alias="personal"), path=path)
        add_contact("lead", "lead@example.com", path=path)
        add_group("team", ["lead", "boss@example.com"], path=path)
        book = load_config(path).address_book
        assert book["lead"] == ("lead@example.com",)
        assert book["team"] == ("lead", "boss@example.com")

    def test_add_contact_rejects_a_name_that_looks_like_an_address(self, tmp_path):
        with pytest.raises(InvalidAddressError, match="plain alias"):
            add_contact("me@x.com", "lead@example.com", path=tmp_path / "config.toml")

    def test_add_account_refuses_the_old_account_table_layout(self, tmp_path):
        # A config written before the `[[accounts]]` switch: the writer cannot append an
        # array-of-tables named `accounts` beside a table of the same name. It must
        # refuse with advice -- a ConfigError, inside the catch surface -- and leave the
        # file exactly as it was, not half-edit or corrupt it.
        path = _write_config(tmp_path, TWO_ACCOUNT_CONFIG)
        before = path.read_text(encoding="utf-8")
        with pytest.raises(ConfigError, match=r"old \[accounts.<name>\] layout"):
            add_account(account_for("new@gmail.com", alias="work"), path=path)
        assert path.read_text(encoding="utf-8") == before

    def test_add_contact_wraps_an_edit_collision_as_config_error(self, tmp_path):
        # `contacts` already exists as a top-level key, so opening a `[contacts]` table
        # to add an alias is a name collision tomlite refuses -- as a ConfigError here,
        # not tomlite's own exception type escaping the catch surface.
        path = _write_config(
            tmp_path,
            'contacts = "oops"\n[[accounts]]\nemail = "me@naver.com"\nalias = "me"\n',
        )
        with pytest.raises(ConfigError, match="cannot update the configuration"):
            add_contact("lead", "lead@example.com", path=path)

    def test_add_contact_wraps_an_unsupported_file_as_config_error(self, tmp_path):
        # An existing file tomlite will not edit safely (a dotted key): it loads, but
        # the first edit is refused. That refusal must surface as a ConfigError too.
        path = _write_config(tmp_path, "a.b = 1\n")
        with pytest.raises(ConfigError, match="cannot update the configuration"):
            add_contact("lead", "lead@example.com", path=path)


class TestAddressBook:
    def test_string_contact_becomes_a_one_entry_alias(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.address_book["lead-naver"] == ("lead@naver.com",)

    def test_list_contact_becomes_a_group(self, tmp_path):
        config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
        assert config.address_book["team"] == ("me@naver.com", "lead")

    def test_address_book_may_be_absent(self, tmp_path):
        path = _write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n',
        )
        assert load_config(path).address_book == {}

    def test_contact_of_the_wrong_shape_is_refused(self, tmp_path):
        path = _write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            "[contacts]\nlead = 42\n",
        )
        with pytest.raises(ConfigError, match="must be a string or a list"):
            load_config(path)

    def test_quoted_alias_may_contain_a_dot(self, tmp_path):
        path = _write_config(
            tmp_path,
            '[accounts.naver]\nprovider = "naver"\nusername = "me@naver.com"\n'
            '[contacts]\n"jane.doe" = "jane@example.com"\n',
        )
        config = load_config(path)
        assert config.address_book["jane.doe"] == ("jane@example.com",)

    def test_quoted_dotted_aliases_can_be_grouped(self, tmp_path):
        path = _write_config(
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
        path = _write_config(
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
        assert "set-password" in message  # how to create it

    def test_malformed_toml_is_reported_as_such(self, tmp_path):
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(_write_config(tmp_path, "this is not toml ["))

    def test_file_without_accounts_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="no accounts"):
            load_config(_write_config(tmp_path, 'default_account = "naver"\n'))


class TestARetiredDefaultsTable:
    """`[defaults]` used to name recipients for a call that named none.

    It is gone, and the danger now is silence: a file that still carries the
    table would load fine and do nothing with it, and its author would go on
    believing their cc was configured. Config that is read but not honoured is
    worse than config that is refused, so it is refused.
    """

    def _with_defaults(self, tmp_path, table):
        return _write_config(tmp_path, f"{TWO_ACCOUNT_CONFIG}\n{table}")

    def test_it_is_refused_rather_than_ignored(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[defaults\]"):
            load_config(self._with_defaults(tmp_path, "[defaults]\nto = 'lead'\n"))

    def test_the_message_says_what_to_do_instead(self, tmp_path):
        with pytest.raises(ConfigError) as caught:
            load_config(self._with_defaults(tmp_path, "[defaults]\ncc = 'lead'\n"))
        message = str(caught.value)
        assert "send(to=" in message  # name them on the call
        assert "delete the table" in message

    def test_an_empty_table_is_refused_too(self, tmp_path):
        # It still means its author expects defaults to work.
        with pytest.raises(ConfigError, match=r"\[defaults\]"):
            load_config(self._with_defaults(tmp_path, "[defaults]\n"))


class TestDefaultLocation:
    def test_config_lives_under_the_xdg_directory_not_the_project(self, monkeypatch):
        monkeypatch.delenv("MAILMAIL_CONFIG", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: Path("/home/tester"))
        expected = Path("/home/tester/.config/mailmail/config.toml")
        assert default_config_path() == expected

    def test_xdg_config_home_is_honoured(self, monkeypatch):
        monkeypatch.delenv("MAILMAIL_CONFIG", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_config_path()) == "/elsewhere/config/mailmail/config.toml"

    def test_explicit_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_CONFIG", "/tmp/other.toml")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_config_path()) == "/tmp/other.toml"

    def test_a_tilde_in_the_override_is_expanded(self, tmp_path, monkeypatch):
        # The positive counterpart to the unresolvable-`~user` error case: a valid
        # `~/x` in the override expands to an absolute path under home. HOME is
        # isolated so the assertion does not depend on the real home directory.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("MAILMAIL_CONFIG", "~/mail/config.toml")
        assert default_config_path() == tmp_path / "mail" / "config.toml"


class TestConfigDir:
    def test_falls_back_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert config_dir() == Path.home() / ".config" / "mailmail"

    def test_uses_absolute_xdg_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert config_dir() == tmp_path / "mailmail"

    def test_expands_a_tilde_in_xdg_home(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "~/somewhere")
        assert config_dir() == Path.home() / "somewhere" / "mailmail"

    def test_ignores_a_relative_xdg_home(self, monkeypatch):
        # The XDG spec requires a relative value be ignored; using it would resolve
        # against the working directory and split a cron run from an interactive one.
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
        assert config_dir() == Path.home() / ".config" / "mailmail"

    def test_ignores_a_blank_xdg_home(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "   ")
        assert config_dir() == Path.home() / ".config" / "mailmail"

    def test_ignores_an_empty_xdg_home(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        assert config_dir() == Path.home() / ".config" / "mailmail"

    def test_ignores_an_unresolvable_tilde_user_without_raising(
        self, unresolvable_tilde, monkeypatch
    ):
        # `~nouser/x` cannot be expanded (no such user), which makes Path.expanduser
        # raise RuntimeError; the value stays relative, so it is ignored, not fatal.
        monkeypatch.setenv("XDG_CONFIG_HOME", f"{unresolvable_tilde}/config")
        assert config_dir() == Path.home() / ".config" / "mailmail"

    def test_home_resolution_failure_is_a_config_error_not_runtime_error(
        self, monkeypatch
    ):
        # HOME unset AND no passwd entry (a container run as an arbitrary uid) makes
        # Path.home() raise RuntimeError. config_dir converts it to ConfigError so no
        # bare RuntimeError escapes send()'s documented MailmailError catch surface.
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        def no_home():
            raise RuntimeError("Could not determine home directory")

        monkeypatch.setattr(Path, "home", no_home)
        with pytest.raises(ConfigError, match="no home directory"):
            config_dir()


class TestUnresolvableTildeUserInANamedPath:
    def test_config_override_with_unresolvable_tilde_user_is_config_error(
        self, unresolvable_tilde, monkeypatch
    ):
        # An explicit MAILMAIL_CONFIG is not silently dropped like a bad XDG value;
        # the unresolvable `~user` surfaces as ConfigError, not a bare RuntimeError.
        monkeypatch.setenv("MAILMAIL_CONFIG", f"{unresolvable_tilde}/config.toml")
        with pytest.raises(ConfigError, match="names no home directory"):
            load_config()

    def test_explicit_path_with_unresolvable_tilde_user_is_config_error(
        self, unresolvable_tilde
    ):
        with pytest.raises(ConfigError, match="names no home directory"):
            load_config(f"{unresolvable_tilde}/config.toml")


def test_config_dir_is_exported_at_the_top_level():
    # config_dir is public: a caller resolves the directory the same way mailmail
    # does. Guard the re-export so dropping the import or the __all__ entry fails.
    import mailmail

    assert mailmail.config_dir is config_dir
    assert "config_dir" in mailmail.__all__


def test_a_non_utf8_config_is_a_config_error(tmp_path):
    # A config saved in cp949/EUC-KR (a realistic mistake) must surface as
    # ConfigError, not a bare UnicodeDecodeError that escapes send()'s catch.
    path = tmp_path / "config.toml"
    path.write_bytes('default_account = "네이버"\n'.encode("cp949"))
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(path)


def test_a_tilde_in_an_explicit_path_is_expanded(tmp_path, monkeypatch):
    # The positive counterpart to the unresolvable-`~user` case: `~` (expanded via
    # $HOME) resolves an explicit path to a real file that then loads.
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_config(tmp_path, TWO_ACCOUNT_CONFIG)
    config = load_config("~/config.toml")
    assert config.default_account == "naver"


def test_resolve_account_does_not_treat_an_empty_name_as_the_default(tmp_path):
    # `name or default` would redirect an explicit "" to the default; an explicitly
    # named account must be looked up, not silently swapped for the default.
    config = load_config(_write_config(tmp_path, TWO_ACCOUNT_CONFIG))
    with pytest.raises(UnknownAccountError, match="''"):
        config.resolve_account("")


def test_a_line_break_in_a_username_is_refused(tmp_path):
    # username becomes the From header; a newline would break it or inject another.
    toml = (
        'default_account = "naver"\n'
        "[accounts.naver]\n"
        'provider = "naver"\n'
        'username = "me\\nBcc: attacker@example.com"\n'
    )
    with pytest.raises(ConfigError, match="line break"):
        load_config(_write_config(tmp_path, toml))


def test_a_config_path_that_is_a_directory_is_a_config_error(tmp_path):
    # A directory makes read_text raise IsADirectoryError (an OSError); it must
    # surface as ConfigError, not escape send()'s catch as a bare OSError.
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_config(tmp_path)
