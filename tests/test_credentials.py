"""Storing passwords: found when wanted, and never left readable by others."""

import json
import os
import stat
from pathlib import Path

import pytest

from mailmail.account import SmtpAccount
from mailmail.credentials import (
    CREDENTIALS_FILE_MODE,
    default_credentials_path,
    delete_password,
    resolve_password,
    store_password,
)
from mailmail.errors import (
    CredentialsError,
    InsecureCredentialsError,
    MailmailError,
    MissingPasswordError,
)
from mailmail.provider import GMAIL, NAVER

NAVER_ACCOUNT = SmtpAccount(name="naver", username="me@naver.com", provider=NAVER)
GMAIL_ACCOUNT = SmtpAccount(name="gmail", username="me@gmail.com", provider=GMAIL)

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="the file mode is only real on POSIX"
)


@pytest.fixture
def credentials_path(tmp_path, monkeypatch):
    """Point the store at a throwaway file, never the operator's real one."""
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("MAILMAIL_CREDENTIALS", str(path))
    monkeypatch.delenv("MAILMAIL_PASSWORD", raising=False)
    return path


def mode_of(path):
    return stat.S_IMODE(path.stat().st_mode)


class TestStoreAndResolve:
    def test_password_survives_a_store_then_resolve(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        assert resolve_password(NAVER_ACCOUNT) == "app-password"

    def test_stored_password_is_read_back_by_a_later_process(self, credentials_path):
        # The point of the file: it outlives the process that wrote it, so the
        # password is entered once rather than once per send.
        store_password(NAVER_ACCOUNT, "app-password")
        assert json.loads(credentials_path.read_text()) == {
            "me@naver.com": "app-password"
        }

    def test_accounts_do_not_overwrite_each_other(self, credentials_path):
        store_password(NAVER_ACCOUNT, "naver-password")
        store_password(GMAIL_ACCOUNT, "gmail-password")
        assert resolve_password(NAVER_ACCOUNT) == "naver-password"
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"

    def test_storing_again_replaces_the_password(self, credentials_path):
        store_password(NAVER_ACCOUNT, "old-password")
        store_password(NAVER_ACCOUNT, "new-password")
        assert resolve_password(NAVER_ACCOUNT) == "new-password"

    def test_empty_password_is_refused_rather_than_stored(self, credentials_path):
        # Storing it would make resolve_password report "no password stored" for
        # an account that does have an entry -- a contradiction the reader cannot
        # act on.
        with pytest.raises(CredentialsError, match="empty password"):
            store_password(NAVER_ACCOUNT, "")

    def test_whitespace_only_password_is_refused(self, credentials_path):
        with pytest.raises(CredentialsError, match="empty password"):
            store_password(NAVER_ACCOUNT, "   \n")

    def test_refusing_an_empty_password_leaves_the_store_untouched(
        self, credentials_path
    ):
        store_password(NAVER_ACCOUNT, "app-password")
        with pytest.raises(CredentialsError):
            store_password(NAVER_ACCOUNT, "")
        assert resolve_password(NAVER_ACCOUNT) == "app-password"

    def test_pasted_trailing_newline_is_dropped(self, credentials_path):
        # A password copied out of a browser almost always brings one along.
        store_password(NAVER_ACCOUNT, "APP1PASSWORD\n")
        assert resolve_password(NAVER_ACCOUNT) == "APP1PASSWORD"

    def test_surrounding_spaces_are_dropped(self, credentials_path):
        store_password(NAVER_ACCOUNT, "  APP1PASSWORD  ")
        assert resolve_password(NAVER_ACCOUNT) == "APP1PASSWORD"

    def test_internal_spaces_are_kept(self, credentials_path):
        # Gmail displays its app password in four space-separated groups; whether
        # to strip those is the user's call, not ours to guess.
        store_password(NAVER_ACCOUNT, "abcd efgh ijkl mnop")
        assert resolve_password(NAVER_ACCOUNT) == "abcd efgh ijkl mnop"

    def test_password_with_awkward_characters_survives_verbatim(
        self, credentials_path
    ):
        # The reason this store is JSON: hand-rolled TOML escaping mangles these.
        awkward = 'quote" backslash\\ brace} 한글 \n newline'
        store_password(NAVER_ACCOUNT, awkward)
        assert resolve_password(NAVER_ACCOUNT) == awkward

    def test_missing_password_names_the_account_and_the_file(self, credentials_path):
        with pytest.raises(MissingPasswordError) as caught:
            resolve_password(NAVER_ACCOUNT)
        message = str(caught.value)
        assert "me@naver.com" in message
        assert str(credentials_path) in message

    def test_account_without_a_password_does_not_borrow_anothers(
        self, credentials_path
    ):
        store_password(GMAIL_ACCOUNT, "gmail-password")
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)


class TestEnvironmentOverride:
    def test_env_var_wins_over_the_file(self, credentials_path, monkeypatch):
        store_password(NAVER_ACCOUNT, "from-file")
        monkeypatch.setenv("MAILMAIL_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"

    def test_env_var_alone_needs_no_file_at_all(self, credentials_path, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"
        assert not credentials_path.exists()


@posix_only
class TestFilePermissions:
    def test_new_file_is_owner_only_from_the_moment_it_exists(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        assert mode_of(credentials_path) == CREDENTIALS_FILE_MODE

    def test_loosened_permissions_are_tightened_on_the_next_write(
        self, credentials_path
    ):
        # Storing repairs a loose file rather than refusing it: refusing would
        # leave the password sitting there readable, which helps nobody.
        store_password(NAVER_ACCOUNT, "app-password")
        credentials_path.chmod(0o644)
        store_password(GMAIL_ACCOUNT, "another-password")
        assert mode_of(credentials_path) == CREDENTIALS_FILE_MODE

    def test_repairing_the_mode_keeps_the_existing_passwords(self, credentials_path):
        store_password(NAVER_ACCOUNT, "naver-password")
        credentials_path.chmod(0o644)
        store_password(GMAIL_ACCOUNT, "gmail-password")
        assert resolve_password(NAVER_ACCOUNT) == "naver-password"
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"

    def test_deleting_also_tightens_a_loosened_file(self, credentials_path):
        store_password(NAVER_ACCOUNT, "naver-password")
        store_password(GMAIL_ACCOUNT, "gmail-password")
        credentials_path.chmod(0o644)
        delete_password(GMAIL_ACCOUNT)
        assert mode_of(credentials_path) == CREDENTIALS_FILE_MODE

    def test_world_readable_file_is_refused_rather_than_read(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        credentials_path.chmod(0o644)
        with pytest.raises(InsecureCredentialsError, match="chmod 600"):
            resolve_password(NAVER_ACCOUNT)

    def test_group_readable_file_is_refused_too(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        credentials_path.chmod(0o640)
        with pytest.raises(InsecureCredentialsError):
            resolve_password(NAVER_ACCOUNT)

    def test_password_is_never_on_disk_readable_by_others(self, credentials_path):
        # The store used to be opened with O_TRUNC on the real path: O_CREAT
        # applies its mode only to a *new* file, so writing into a store that had
        # been loosened to 0644 put the password there at 0644 and tightened it
        # only afterwards. Writing a fresh 0600 file and renaming closes that.
        credentials_path.write_text("{}")
        credentials_path.chmod(0o644)
        store_password(NAVER_ACCOUNT, "app-password")
        assert mode_of(credentials_path) == CREDENTIALS_FILE_MODE


@posix_only  # the setup chmods; only the value of `os.name` is being faked
class TestWhereTheFileModeIsNotReal:
    """Windows reports a mode that no chmod ever set, and the check believed it.

    `os.stat` there synthesises `st_mode` from the read-only attribute alone --
    0o666 for an ordinary file, 0o444 for a read-only one -- so a test for group
    or other bits matches every file that exists. The package was therefore
    unusable on Windows in a way no test noticed: `store_password` wrote the
    file, `resolve_password` refused to read the very same file, and the refusal
    said to run `chmod`, which Windows does not have. `os.chmod` there "can only
    set the file's read-only flag... All other bits are ignored" (CPython os
    docs), so there was no mode for anyone to fix.
    """

    # `path=` is passed rather than left to default, because faking `os.name` is
    # a blunter instrument than it looks: `Path(...)` picks WindowsPath over
    # PosixPath by reading it, so the default lookup would come back as
    # `\tmp\...` and fail to find a file for reasons having nothing to do with
    # the mode. Handing the path in keeps the fake pointed at the one thing under
    # test.

    def test_a_mode_posix_would_refuse_is_read_anyway(
        self, credentials_path, monkeypatch
    ):
        store_password(NAVER_ACCOUNT, "app-password")
        credentials_path.chmod(0o644)  # 0o666 is what Windows would report here
        monkeypatch.setattr(os, "name", "nt")
        got = resolve_password(NAVER_ACCOUNT, path=credentials_path)
        assert got == "app-password"

    def test_posix_still_refuses_that_same_file(self, credentials_path, monkeypatch):
        # Guards the guard: the skip has to be about the platform. If the check
        # itself had rotted, the test above would pass for the wrong reason.
        store_password(NAVER_ACCOUNT, "app-password")
        credentials_path.chmod(0o644)
        monkeypatch.setattr(os, "name", "posix")
        with pytest.raises(InsecureCredentialsError, match="chmod 600"):
            resolve_password(NAVER_ACCOUNT, path=credentials_path)

    def test_write_leaves_no_temporary_file_behind(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        leftovers = list(credentials_path.parent.glob("*.tmp"))
        assert leftovers == []

    def test_parent_directory_is_created_when_absent(self, tmp_path, monkeypatch):
        nested = tmp_path / "fresh" / "mailmail" / "credentials.json"
        monkeypatch.setenv("MAILMAIL_CREDENTIALS", str(nested))
        store_password(NAVER_ACCOUNT, "app-password")
        assert nested.exists()


class TestWriteIsAllOrNothing:
    """A half-written store is worse than no write at all.

    The old write emptied the real file first, so a crash between the truncate
    and the dump left an empty store -- saving one account's password destroyed
    every other account's.
    """

    def test_a_failed_write_leaves_the_previous_store_intact(
        self, credentials_path, monkeypatch
    ):
        store_password(NAVER_ACCOUNT, "naver-password")

        def explode(*_args, **_kwargs):
            raise OSError("disk full, mid-write")

        monkeypatch.setattr("mailmail.credentials.json.dump", explode)
        with pytest.raises(OSError):
            store_password(GMAIL_ACCOUNT, "gmail-password")

        # Reading is unaffected by the patched writer, so the store can be read
        # back without undoing it -- undoing would also drop the fixture's
        # redirect and send this at the operator's real credentials.
        assert resolve_password(NAVER_ACCOUNT) == "naver-password"

    def test_a_failed_write_leaves_no_temporary_file_behind(
        self, credentials_path, monkeypatch
    ):
        store_password(NAVER_ACCOUNT, "naver-password")

        def explode(*_args, **_kwargs):
            raise OSError("disk full, mid-write")

        monkeypatch.setattr("mailmail.credentials.json.dump", explode)
        with pytest.raises(OSError):
            store_password(GMAIL_ACCOUNT, "gmail-password")

        assert list(credentials_path.parent.glob("*.tmp")) == []


class TestDelete:
    def test_deleting_removes_only_that_account(self, credentials_path):
        store_password(NAVER_ACCOUNT, "naver-password")
        store_password(GMAIL_ACCOUNT, "gmail-password")
        delete_password(NAVER_ACCOUNT)
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)

    def test_deleting_an_absent_password_is_not_an_error(self, credentials_path):
        delete_password(NAVER_ACCOUNT)

    def test_deleting_twice_is_not_an_error(self, credentials_path):
        store_password(NAVER_ACCOUNT, "app-password")
        delete_password(NAVER_ACCOUNT)
        delete_password(NAVER_ACCOUNT)


class TestMalformedFile:
    def test_file_that_is_not_json_is_reported_as_such(self, credentials_path):
        credentials_path.write_text("this is not json")
        credentials_path.chmod(CREDENTIALS_FILE_MODE)
        with pytest.raises(CredentialsError, match="not valid JSON"):
            resolve_password(NAVER_ACCOUNT)

    def test_json_of_the_wrong_shape_is_refused(self, credentials_path):
        credentials_path.write_text('["not", "a", "mapping"]')
        credentials_path.chmod(CREDENTIALS_FILE_MODE)
        with pytest.raises(CredentialsError, match="map each email address"):
            resolve_password(NAVER_ACCOUNT)

    def test_non_string_password_is_refused(self, credentials_path):
        credentials_path.write_text('{"me@naver.com": 12345}')
        credentials_path.chmod(CREDENTIALS_FILE_MODE)
        with pytest.raises(CredentialsError, match="map each email address"):
            resolve_password(NAVER_ACCOUNT)

    def test_a_non_utf8_store_is_a_credentials_error(self, credentials_path):
        # A store saved in cp949/EUC-KR must surface as CredentialsError, not a bare
        # UnicodeDecodeError that escapes send()'s documented catch surface.
        credentials_path.write_bytes('{"me@naver.com": "네이버비밀"}'.encode("cp949"))
        credentials_path.chmod(CREDENTIALS_FILE_MODE)
        with pytest.raises(CredentialsError, match="UTF-8"):
            resolve_password(NAVER_ACCOUNT)

    def test_a_store_path_that_is_a_directory_is_a_credentials_error(self, tmp_path):
        # A directory makes read_text raise IsADirectoryError (an OSError); it must
        # surface as CredentialsError, not escape send()'s catch as a bare OSError.
        with pytest.raises(CredentialsError, match="cannot be read"):
            store_password(NAVER_ACCOUNT, "pw", path=tmp_path)


class TestDefaultLocation:
    def test_credentials_sit_beside_the_config_not_in_the_project(self, monkeypatch):
        monkeypatch.delenv("MAILMAIL_CREDENTIALS", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_credentials_path()) == (
            "/elsewhere/config/mailmail/credentials.json"
        )

    def test_explicit_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_CREDENTIALS", "/tmp/other.json")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_credentials_path()) == "/tmp/other.json"

    def test_a_relative_xdg_home_never_places_the_secret_under_the_cwd(
        self, monkeypatch
    ):
        # The secret file is the reason the relative-XDG guard exists: a relative
        # value would drop credentials.json wherever the process happened to start.
        monkeypatch.delenv("MAILMAIL_CREDENTIALS", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
        assert default_credentials_path() == (
            Path.home() / ".config" / "mailmail" / "credentials.json"
        )

    def test_credentials_override_with_unresolvable_tilde_user_is_credentials_error(
        self, monkeypatch
    ):
        # An explicit MAILMAIL_CREDENTIALS is not silently dropped; the unresolvable
        # `~user` surfaces as CredentialsError, not a bare RuntimeError.
        monkeypatch.setenv("MAILMAIL_CREDENTIALS", "~nosuchuser_zzz/credentials.json")
        with pytest.raises(CredentialsError, match="names no home directory"):
            default_credentials_path()

    def test_a_tilde_in_the_override_is_expanded(self, monkeypatch):
        # Positive counterpart: a valid `~/x` override expands under home.
        monkeypatch.setenv("MAILMAIL_CREDENTIALS", "~/mail/credentials.json")
        assert default_credentials_path() == (
            Path.home() / "mail" / "credentials.json"
        )

    def test_home_resolution_failure_stays_inside_the_mailmail_error_surface(
        self, monkeypatch
    ):
        # The secret path shares config_dir(), so a HOME-less container must not leak
        # a bare RuntimeError here either -- it surfaces as a MailmailError subclass
        # (ConfigError) that send()'s documented catch still holds.
        monkeypatch.delenv("MAILMAIL_CREDENTIALS", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        def no_home():
            raise RuntimeError("Could not determine home directory")

        monkeypatch.setattr(Path, "home", no_home)
        with pytest.raises(MailmailError):
            default_credentials_path()


class TestTheEnvironmentPasswordKnowsWhichAccountItIsFor:
    """One exported password used to answer for every provider.

    `resolve_password(account)` read the bare `MAILMAIL_PASSWORD` and returned
    before it ever looked at `account`. With two mailboxes configured -- which
    the config file expects, demanding `default_account` once there are two --
    exporting a Gmail app password for a one-off send and then sending as naver
    from the same shell transmitted the Gmail secret to Naver's server, where it
    lands in a failed-auth log. The send fails 535, so nobody learns of it.
    """

    def test_the_account_specific_name_wins(self, credentials_path, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "the-gmail-one")
        monkeypatch.setenv("MAILMAIL_PASSWORD_NAVER", "the-naver-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-naver-one"

    def test_each_account_gets_its_own(self, credentials_path, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD_NAVER", "the-naver-one")
        monkeypatch.setenv("MAILMAIL_PASSWORD_GMAIL", "the-gmail-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-naver-one"
        assert resolve_password(GMAIL_ACCOUNT) == "the-gmail-one"

    def test_one_accounts_secret_does_not_answer_for_another(
        self, credentials_path, monkeypatch
    ):
        """The disclosure, stated as the thing that must not happen."""
        monkeypatch.setenv("MAILMAIL_PASSWORD_GMAIL", "the-gmail-one")
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)

    def test_the_bare_name_still_serves_when_it_is_all_there_is(
        self, credentials_path, monkeypatch
    ):
        # The single-account case, and the shared-password case: unchanged.
        monkeypatch.setenv("MAILMAIL_PASSWORD", "the-only-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-only-one"
        assert resolve_password(GMAIL_ACCOUNT) == "the-only-one"
