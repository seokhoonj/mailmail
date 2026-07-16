"""Storing passwords: found when wanted, and never left readable by others."""

import json
import stat

import pytest

from mailrun.account import SmtpAccount
from mailrun.credentials import (
    CREDENTIALS_FILE_MODE,
    default_credentials_path,
    delete_password,
    resolve_password,
    store_password,
)
from mailrun.errors import (
    CredentialsError,
    InsecureCredentialsError,
    MissingPasswordError,
)
from mailrun.provider import GMAIL, NAVER

NAVER_ACCOUNT = SmtpAccount(name="naver", username="me@naver.com", provider=NAVER)
GMAIL_ACCOUNT = SmtpAccount(name="gmail", username="me@gmail.com", provider=GMAIL)


@pytest.fixture
def credentials_path(tmp_path, monkeypatch):
    """Point the store at a throwaway file, never the operator's real one."""
    path = tmp_path / "credentials.json"
    monkeypatch.setenv("MAILRUN_CREDENTIALS", str(path))
    monkeypatch.delenv("MAILRUN_PASSWORD", raising=False)
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
        monkeypatch.setenv("MAILRUN_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"

    def test_env_var_alone_needs_no_file_at_all(self, credentials_path, monkeypatch):
        monkeypatch.setenv("MAILRUN_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"
        assert not credentials_path.exists()


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

    def test_parent_directory_is_created_when_absent(self, tmp_path, monkeypatch):
        nested = tmp_path / "fresh" / "mailrun" / "credentials.json"
        monkeypatch.setenv("MAILRUN_CREDENTIALS", str(nested))
        store_password(NAVER_ACCOUNT, "app-password")
        assert nested.exists()


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


class TestDefaultLocation:
    def test_credentials_sit_beside_the_config_not_in_the_project(self, monkeypatch):
        monkeypatch.delenv("MAILRUN_CREDENTIALS", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_credentials_path()) == (
            "/elsewhere/config/mailrun/credentials.json"
        )

    def test_explicit_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("MAILRUN_CREDENTIALS", "/tmp/other.json")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/elsewhere/config")
        assert str(default_credentials_path()) == "/tmp/other.json"
