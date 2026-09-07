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
trailing newline -- is xdg-kit's. This module maps an account onto that store: the
file is keyed by the account's username (the email address), while the environment is
keyed by the account *name*, because the two answer different questions (see
`_load_password_from_env`).
"""

from __future__ import annotations

import os
import re

from xdg_kit import XdgKitError, get_secret, set_secret, unset_secret

from mailmail.account import SmtpAccount
from mailmail.errors import CredentialsError, MissingPasswordError

__all__ = [
    "PASSWORD_ENV_VAR",
    "delete_password",
    "resolve_password",
    "store_password",
]

# The xdg-kit app whose store the passwords live in:
# ~/.config/mailmail/credentials.json.
_STORE_APP = "mailmail"

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
        # override carries mailmail's own env resolution; xdg-kit also probes an env
        # var named the username, a no-op for an email address (never a valid shell
        # variable name).
        password = get_secret(
            _STORE_APP, account.username, override=_load_password_from_env(account)
        )
    except XdgKitError as err:
        raise CredentialsError(
            f"the mailmail credential store could not be read: {err}"
        ) from err
    if password:
        return password
    raise MissingPasswordError(
        f"no password stored for {account.username}; store the app password from "
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
        set_secret(_STORE_APP, account.username, value=password)
    except ValueError as err:  # set_secret's only ValueError is the blank-value refuse
        raise CredentialsError(
            f"refusing to store an empty password for {account.username}; paste the "
            f"app password from {account.provider.name}, or call "
            f"delete_password(account) to remove the entry"
        ) from err
    except XdgKitError as err:
        raise CredentialsError(
            f"could not store the password for {account.username}: {err}"
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
        unset_secret(_STORE_APP, account.username)
    except XdgKitError as err:
        raise CredentialsError(
            f"could not remove the password for {account.username}: {err}"
        ) from err


def _load_password_from_env(account: SmtpAccount) -> str | None:
    """The password the environment offers for this account, if any.

    `MAILMAIL_PASSWORD_NAVER` beats a bare `MAILMAIL_PASSWORD`, because the bare name
    is only unambiguous while one account exists. Reading the bare name first, with
    gmail and naver both configured, would hand one exported password to whichever
    server was asked -- a disclosure (the Gmail app password lands in Naver's
    failed-auth log), not an inconvenience. The bare name still works, and is still the
    right thing to export when one account is configured or every account shares a
    password.

    Anything a shell will not take in a variable name folds to `_`, so the name that is
    read is the name that can be exported: `[accounts.me-naver]` is ordinary, but
    `export MAILMAIL_PASSWORD_ME-NAVER=...` is not a valid identifier, so the literal
    name could never match and would fall to the bare name -- the disclosure above,
    again.
    """
    suffix = re.sub(r"[^A-Z0-9]", "_", account.name.upper())
    per_account = os.environ.get(f"{PASSWORD_ENV_VAR}_{suffix}")
    return per_account or os.environ.get(PASSWORD_ENV_VAR)
