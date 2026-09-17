"""mailmail's password handling: environment resolution, account mapping, and errors.

The store itself -- the file location, 0600 mode, atomic write, whitespace stripping,
and blank rejection -- is credbox's and tested there. These cover only what mailmail
adds on top: resolving the environment before the file, keying both by the account's
handle (falling back to the address for a password stored before the key was the
handle), and wrapping the store's failures in mailmail's own error type.
"""

import os

import pytest
from credbox import CredBoxError, config_dir

from mailmail.account import SmtpAccount
from mailmail.credentials import delete_password, resolve_password, store_password
from mailmail.errors import CredentialsError, MissingPasswordError
from mailmail.provider import GMAIL, NAVER

NAVER_ACCOUNT = SmtpAccount(email="me@naver.com", alias="naver", provider=NAVER)
GMAIL_ACCOUNT = SmtpAccount(email="me@gmail.com", alias="gmail", provider=GMAIL)


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Redirect the credential store into tmp_path and clear every MAILMAIL_PASSWORD*
    var, so a test neither touches the operator's real store nor inherits a password."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for key in [k for k in os.environ if k.startswith("MAILMAIL_PASSWORD")]:
        monkeypatch.delenv(key, raising=False)


def _store_path():
    return config_dir("mailmail") / "credentials.json"


class TestStoreAndResolve:
    def test_password_survives_a_store_then_resolve(self):
        store_password(NAVER_ACCOUNT, "app-password")
        assert resolve_password(NAVER_ACCOUNT) == "app-password"

    def test_the_store_is_keyed_by_the_handle(self):
        # Two accounts share a mailbox but have different handles; a password stored
        # under one does not resolve for the other, because the key is the handle.
        store_password(NAVER_ACCOUNT, "app-password")  # handle "naver"
        other = SmtpAccount(email="me@naver.com", alias="naver-alias", provider=NAVER)
        with pytest.raises(MissingPasswordError):
            resolve_password(other)

    def test_a_password_stored_under_the_address_still_resolves(self):
        # Read-compat: a password set before the key became the handle was filed under
        # the address, so an aliased account falls back to the address to find it.
        from credbox import Credentials

        Credentials.for_app("mailmail").set("me@naver.com", value="legacy-password")
        assert resolve_password(NAVER_ACCOUNT) == "legacy-password"

    def test_accounts_do_not_overwrite_each_other(self):
        store_password(NAVER_ACCOUNT, "naver-password")
        store_password(GMAIL_ACCOUNT, "gmail-password")
        assert resolve_password(NAVER_ACCOUNT) == "naver-password"
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"

    def test_storing_again_replaces_the_password(self):
        store_password(NAVER_ACCOUNT, "old-password")
        store_password(NAVER_ACCOUNT, "new-password")
        assert resolve_password(NAVER_ACCOUNT) == "new-password"

    def test_empty_password_is_refused_as_a_credentials_error(self):
        with pytest.raises(CredentialsError, match="empty password"):
            store_password(NAVER_ACCOUNT, "")

    def test_whitespace_only_password_is_refused(self):
        with pytest.raises(CredentialsError, match="empty password"):
            store_password(NAVER_ACCOUNT, "   \n")

    def test_refusing_an_empty_password_leaves_the_store_untouched(self):
        store_password(NAVER_ACCOUNT, "app-password")
        with pytest.raises(CredentialsError):
            store_password(NAVER_ACCOUNT, "")
        assert resolve_password(NAVER_ACCOUNT) == "app-password"

    def test_a_pasted_trailing_newline_is_dropped(self):
        # mailmail leans on the store to strip a value copied out of a browser.
        store_password(NAVER_ACCOUNT, "APP1PASSWORD\n")
        assert resolve_password(NAVER_ACCOUNT) == "APP1PASSWORD"

    def test_internal_spaces_are_kept(self):
        # Gmail shows its app password in four space-separated groups; only edges go.
        store_password(NAVER_ACCOUNT, "abcd efgh ijkl mnop")
        assert resolve_password(NAVER_ACCOUNT) == "abcd efgh ijkl mnop"

    def test_missing_password_names_the_account(self):
        with pytest.raises(MissingPasswordError) as caught:
            resolve_password(NAVER_ACCOUNT)
        assert "no password stored for naver" in str(caught.value)  # the handle

    def test_account_without_a_password_does_not_borrow_anothers(self):
        store_password(GMAIL_ACCOUNT, "gmail-password")
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)


class TestEnvironmentOverride:
    def test_env_var_wins_over_the_file(self, monkeypatch):
        store_password(NAVER_ACCOUNT, "from-file")
        monkeypatch.setenv("MAILMAIL_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"

    def test_env_var_alone_needs_no_file_at_all(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "from-env")
        assert resolve_password(NAVER_ACCOUNT) == "from-env"
        assert not _store_path().exists()

    def test_a_blank_env_var_reads_as_absent(self, monkeypatch):
        # An exported-but-empty MAILMAIL_PASSWORD must read as absent, not resolve to ""
        # (SMTP rejects a blank password): it falls through, so with no file it is a
        # clear MissingPasswordError.
        monkeypatch.setenv("MAILMAIL_PASSWORD", "")
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)


class TestDelete:
    def test_deleting_removes_only_that_account(self):
        store_password(NAVER_ACCOUNT, "naver-password")
        store_password(GMAIL_ACCOUNT, "gmail-password")
        delete_password(NAVER_ACCOUNT)
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)

    def test_deleting_an_absent_password_leaves_other_accounts_intact(self):
        store_password(GMAIL_ACCOUNT, "gmail-password")
        delete_password(NAVER_ACCOUNT)   # absent -> a no-op, not an error
        assert resolve_password(GMAIL_ACCOUNT) == "gmail-password"

    def test_deleting_twice_leaves_the_account_without_a_password(self):
        store_password(NAVER_ACCOUNT, "app-password")
        delete_password(NAVER_ACCOUNT)
        delete_password(NAVER_ACCOUNT)
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)


class TestAMalformedStoreSurfacesAsCredentialsError:
    # The store's own read errors are credbox's; mailmail's job is only to wrap them in
    # its own error type so a caller's `except CredentialsError` still holds.
    def _plant(self, contents):
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def test_a_file_that_is_not_json_is_a_credentials_error(self):
        self._plant("this is not json")
        with pytest.raises(CredentialsError):
            resolve_password(NAVER_ACCOUNT)

    def test_json_of_the_wrong_shape_is_a_credentials_error(self):
        self._plant('["not", "a", "mapping"]')
        with pytest.raises(CredentialsError):
            resolve_password(NAVER_ACCOUNT)

    def test_a_non_utf8_store_is_a_credentials_error(self):
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes('{"me@naver.com": "네이버"}'.encode("cp949"))
        with pytest.raises(CredentialsError):
            resolve_password(NAVER_ACCOUNT)


class TestStoreFailuresWrapAsCredentialsError:
    # A store write failure must surface as mailmail's CredentialsError, never a foreign
    # type, and must never carry the password value into the message or the cause chain.
    def test_a_write_failure_hides_the_password(self, monkeypatch):
        secret = "sk-NEVER-LEAK-THIS"

        class FailStore:
            def set(self, *args, **kwargs):
                raise CredBoxError("backend write failed")

        monkeypatch.setattr("mailmail.credentials._get_store", lambda: FailStore())
        with pytest.raises(CredentialsError) as caught:
            store_password(NAVER_ACCOUNT, secret)
        # Walk the whole chain -- __cause__ (explicit `from`) AND __context__ (implicit)
        # -- with a cycle guard, so a password leaked through either link is caught.
        seen: set[int] = set()
        pending: list[BaseException | None] = [caught.value]
        while pending:
            error = pending.pop()
            if error is None or id(error) in seen:
                continue
            seen.add(id(error))
            assert secret not in str(error)
            pending += [error.__cause__, error.__context__]

    def test_a_delete_failure_is_a_credentials_error(self, monkeypatch):
        class FailStore:
            def unset(self, *args, **kwargs):
                raise CredBoxError("backend write failed")

        monkeypatch.setattr("mailmail.credentials._get_store", lambda: FailStore())
        with pytest.raises(CredentialsError):
            delete_password(NAVER_ACCOUNT)

    def test_a_malformed_store_binding_is_a_credentials_error(self, monkeypatch):
        # A bad MAILMAIL_NAMESPACE surfaces at the call as a CredentialsError, not
        # credbox's foreign InvalidAppNameError. (`_reset_store_cache` isolates the
        # lazily-built store, so this binding does not leak to another test.)
        monkeypatch.setenv("MAILMAIL_NAMESPACE", "../evil")
        with pytest.raises(CredentialsError, match="store binding is invalid"):
            resolve_password(NAVER_ACCOUNT)

    def test_import_mailmail_does_not_crash_on_a_malformed_binding(self):
        # The store is built lazily, so `import mailmail` with a bad MAILMAIL_NAMESPACE
        # must succeed -- a credential *call* then raises. An in-process test can't show
        # this (mailmail is already imported at collection), so use a fresh subprocess.
        import os
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "import mailmail; print('imported ok')"],
            capture_output=True,
            text=True,
            env={**os.environ, "MAILMAIL_NAMESPACE": "../evil"},
        )
        assert result.returncode == 0, result.stderr
        assert "imported ok" in result.stdout


class TestTheEnvironmentPasswordKnowsWhichAccountItIsFor:
    """One exported password used to answer for every provider.

    Reading the bare `MAILMAIL_PASSWORD` before looking at the account meant that, with
    two mailboxes configured, exporting a Gmail app password for a one-off send and then
    sending as naver from the same shell transmitted the Gmail secret to Naver's server,
    where it lands in a failed-auth log. The send fails 535, so nobody learns of it. The
    per-account name (`MAILMAIL_PASSWORD_NAVER`) is read first to prevent exactly that.
    """

    def test_the_account_specific_name_wins(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "the-gmail-one")
        monkeypatch.setenv("MAILMAIL_PASSWORD_NAVER", "the-naver-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-naver-one"

    def test_each_account_gets_its_own(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD_NAVER", "the-naver-one")
        monkeypatch.setenv("MAILMAIL_PASSWORD_GMAIL", "the-gmail-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-naver-one"
        assert resolve_password(GMAIL_ACCOUNT) == "the-gmail-one"

    def test_one_accounts_secret_does_not_answer_for_another(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD_GMAIL", "the-gmail-one")
        with pytest.raises(MissingPasswordError):
            resolve_password(NAVER_ACCOUNT)

    def test_the_bare_name_still_serves_when_it_is_all_there_is(self, monkeypatch):
        monkeypatch.setenv("MAILMAIL_PASSWORD", "the-only-one")
        assert resolve_password(NAVER_ACCOUNT) == "the-only-one"
        assert resolve_password(GMAIL_ACCOUNT) == "the-only-one"

    def test_a_hyphen_in_the_account_name_folds_to_an_underscore(self, monkeypatch):
        # `[accounts.me-naver]` -> MAILMAIL_PASSWORD_ME_NAVER; a hyphen is not a legal
        # shell variable char, so folding it keeps the per-account guard reachable.
        account = SmtpAccount(email="me@naver.com", alias="me-naver", provider=NAVER)
        monkeypatch.setenv("MAILMAIL_PASSWORD_ME_NAVER", "the-folded-one")
        assert resolve_password(account) == "the-folded-one"
