"""Where the accounts' passwords are kept.

A password lives in a file only its owner can read, next to the configuration and
well outside any directory that gets synced or committed. This is the shape
`.netrc`, `.pgpass`, and cloud CLI credential files all take, and it is chosen here
for the reason those tools chose it: it never prompts, so a script, a cron job, or an
agent session works the same as a terminal.

The trade is real and worth stating plainly. The file is not encrypted, so it
protects against other users on the machine, not against anything running as you. An
OS keyring would encrypt it -- but only while the keyring is *locked*, and a locked
keyring is precisely what stops to ask for a password. Since an unlocked keyring hands
the secret to anything running as you anyway, the encryption buys little here that the
file permission does not, and it costs the prompt.

What limits the damage is the credential itself: this is an app password, scoped to
sending mail and revocable at the provider without touching the account password.
Store nothing else here.

The store itself -- its location under `config_dir()`, the 0600 file mode, the atomic
read-modify-write, the env-over-file resolution, and stripping a pasted value's
trailing newline -- is credbox's. This module maps an account onto that store: both the
file and the environment are keyed by the account's *handle* (its alias, or its address
when it has none), so an account is named the same way everywhere. A password stored
before the key was the handle -- filed under the address -- is still found, by falling
back to the address on read.
"""

from __future__ import annotations

import os

from credbox import BlankSecretError, CredBoxError, Credentials, env_var_prefix

from mailmail.account import SmtpAccount
from mailmail.errors import CredentialsError, MissingPasswordError

__all__ = [
    "PASSWORD_ENV_VAR",
    "delete_password",
    "resolve_password",
    "store_password",
]

# The credbox app for mailmail's own store, ~/.config/mailmail/credentials.json.
# `for_app` (not the bare `Credentials(...)`) makes mailmail embeddable: a host that
# sets MAILMAIL_STORE_APP / MAILMAIL_NAMESPACE before importing mailmail redirects the
# binding into the host's own store under a "mailmail" section, no code change here.
# credbox resolves the store path per call, so a test repointing XDG_CONFIG_HOME still
# isolates it.
_STORE_APP = "mailmail"
_store = Credentials.for_app(_STORE_APP)

PASSWORD_ENV_VAR = "MAILMAIL_PASSWORD"


def resolve_password(account: SmtpAccount) -> str:
    """Find the account's password: the environment first (so a one-off or a
    container needs no file), then the stored credentials file.

    `MAILMAIL_PASSWORD_<ACCOUNT>` is read before the bare `MAILMAIL_PASSWORD` -- with
    two accounts configured the bare name cannot say which mailbox it is for, and
    answering with it anyway would send one service's app password to the other's
    server (see `_load_password_from_env`).

    Raises
    ------
    MissingPasswordError
        Neither the environment nor the file has a password for this account.
    CredentialsError
        The credential store could not be read or was refused as unsafe (propagated
        from the store).
    """
    try:
        # override carries mailmail's own env resolution. When nothing is found under
        # the handle and the handle is an alias, fall back to the address -- where a
        # password set before the key became the handle was filed.
        env_password = _load_password_from_env(account)
        secret = _store.secret(account.handle, override=env_password)
        if secret is None and account.handle != account.email:
            secret = _store.secret(account.email)
    except CredBoxError as err:
        raise CredentialsError(
            f"the mailmail credential store could not be read: {err}"
        ) from err
    if secret is not None:
        return secret.reveal()
    raise MissingPasswordError(
        f"no password stored for {account.handle}; store the app password from "
        f"{account.provider.name} with store_password(account, password), or set "
        f"{PASSWORD_ENV_VAR}"
    )


def store_password(account: SmtpAccount, password: str) -> None:
    """Store the account's password in the credentials file (created owner-readable
    only, at mode 0600), leaving any other account's password in place. Use the
    provider's app password, not the login password: both Gmail and Naver refuse plain
    SMTP logins on accounts with two-factor sign-in. A pasted value's surrounding
    whitespace is stripped by the store.

    Raises
    ------
    CredentialsError
        The password is empty (stored, it would read back as "no password stored" --
        worse than storing nothing), or the store could not be read or written.
    """
    try:
        _store.set(account.handle, value=password)
    except BlankSecretError as err:
        raise CredentialsError(
            f"refusing to store an empty password for {account.handle}; paste the "
            f"app password from {account.provider.name}, or call "
            f"delete_password(account) to remove the entry"
        ) from err
    except CredBoxError as err:
        raise CredentialsError(
            f"could not store the password for {account.handle}: {err}"
        ) from err


def delete_password(account: SmtpAccount) -> None:
    """Remove the account's password from the credentials file; a no-op when none is
    stored, so revoking is safe to repeat.

    Raises
    ------
    CredentialsError
        The store could not be written (propagated from the store).
    """
    try:
        _store.unset(account.handle)
    except CredBoxError as err:
        raise CredentialsError(
            f"could not remove the password for {account.handle}: {err}"
        ) from err


def _load_password_from_env(account: SmtpAccount) -> str | None:
    """The password the environment offers for this account, if any.

    `MAILMAIL_PASSWORD_ME_NAVER` beats a bare `MAILMAIL_PASSWORD`, because the bare name
    is only unambiguous while one account exists. Reading the bare name first, with
    gmail and naver both configured, would hand one exported password to whichever
    server was asked -- a disclosure (the Gmail app password lands in Naver's
    failed-auth log), not an inconvenience. The bare name still works, and is still the
    right thing to export when one account is configured or every account shares a
    password.

    The per-account suffix folds the handle with credbox's canonical `env_var_prefix`:
    anything a shell will not take in a variable name becomes `_`, so the name that is
    read is the name that can be exported. A handle `me-naver` reads from
    `MAILMAIL_PASSWORD_ME_NAVER`, and an address handle `you@gmail.com` from
    `MAILMAIL_PASSWORD_YOU_GMAIL_COM`. The fold is lossy -- `me-naver` and `me.naver`
    would share one name -- but `load_config` refuses a config whose handles collide
    that way, so no two configured accounts ever reach here with the same suffix.
    """
    suffix = env_var_prefix(account.handle)
    per_account = os.environ.get(f"{PASSWORD_ENV_VAR}_{suffix}")
    return per_account or os.environ.get(PASSWORD_ENV_VAR)
