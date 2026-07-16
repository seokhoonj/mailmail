"""Where the accounts' passwords are kept.

Passwords live in a file only their owner can read, next to the configuration
and well outside any directory that gets synced or committed. This is the shape
`.netrc`, `.pgpass`, and cloud CLI credential files all take, and it is chosen
here for the reason those tools chose it: it never prompts, so a script, a cron
job, or an agent session works the same as a terminal.

The trade is real and worth stating plainly. The file is not encrypted, so it
protects against other users on the machine, not against anything running as you.
An OS keyring would encrypt it -- but only while the keyring is *locked*, and a
locked keyring is precisely what stops to ask for a password. Since an unlocked
keyring hands the secret to anything running as you anyway, the encryption buys
little here that the file permission does not, and it costs the prompt.

What limits the damage is the credential itself: this is an app password, scoped
to sending mail and revocable at the provider without touching the account
password. Store nothing else here.

JSON rather than TOML on purpose: `tomllib` reads TOML but cannot write it, and
hand-rolling TOML string escaping is exactly the kind of thing that corrupts a
password with a backslash in it and fails at 2 a.m. `json` does both halves
correctly.
"""

import json
import os
import stat
from pathlib import Path

from mailrun.account import SmtpAccount
from mailrun.errors import (
    CredentialsError,
    InsecureCredentialsError,
    MissingPasswordError,
)

__all__ = [
    "CREDENTIALS_FILE_MODE",
    "CREDENTIALS_PATH_ENV_VAR",
    "PASSWORD_ENV_VAR",
    "default_credentials_path",
    "delete_password",
    "resolve_password",
    "store_password",
]

PASSWORD_ENV_VAR = "MAILRUN_PASSWORD"
CREDENTIALS_PATH_ENV_VAR = "MAILRUN_CREDENTIALS"

# Owner read/write, nothing for anyone else -- what ssh demands of a private key,
# for the same reason: a secret the group can read is not a secret.
CREDENTIALS_FILE_MODE = 0o600


def default_credentials_path() -> Path:
    """Where mailrun looks for stored passwords.

    `MAILRUN_CREDENTIALS` wins; otherwise it sits beside the configuration, at
    `~/.config/mailrun/credentials.json`.
    """
    override = os.environ.get(CREDENTIALS_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return config_home / "mailrun" / "credentials.json"


def resolve_password(account: SmtpAccount, *, path: Path | None = None) -> str:
    """Find the account's password.

    Checks `MAILRUN_PASSWORD` first, so a one-off or a container can supply the
    password without a file, then the credentials file.

    Raises
    ------
    MissingPasswordError
        Neither source has a password for this account.
    InsecureCredentialsError
        The credentials file is readable by anyone but its owner.
    CredentialsError
        The file exists but is not readable JSON.
    """
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        return from_env
    path = path if path is not None else default_credentials_path()
    # The permission gate belongs here, at the moment a password is trusted --
    # not in the loader, which store and delete also go through. Refusing to
    # *write* a loose file would only strand it loose; rewriting it tightens it.
    if path.exists():
        _check_owner_only_readable(path)
    stored = _load_password_by_username(path).get(account.username)
    if stored:
        return stored
    raise MissingPasswordError(
        f"no password stored for {account.username}; put the app password from "
        f"{account.provider.name} in {path} with store_password(account, password), "
        f"or set {PASSWORD_ENV_VAR}"
    )


def store_password(
    account: SmtpAccount, password: str, *, path: Path | None = None
) -> None:
    """Write the account's password to the credentials file.

    Creates the file owner-readable-only, and leaves any other account's password
    in place. Use the provider's app password, not the account's login password:
    both Gmail and Naver refuse plain SMTP logins on accounts with two-factor
    sign-in.

    Surrounding whitespace is dropped -- a password pasted from a browser almost
    always arrives with a trailing newline, and no server wants it.

    Raises
    ------
    CredentialsError
        The password is empty. Storing it would be worse than storing nothing:
        `resolve_password` would then report "no password stored" for an account
        that does have an entry, sending the reader to look for a missing file.
    """
    password = password.strip()
    if not password:
        raise CredentialsError(
            f"refusing to store an empty password for {account.username}; "
            f"paste the app password from {account.provider.name}, or call "
            f"delete_password(account) to remove the entry"
        )
    path = path if path is not None else default_credentials_path()
    password_by_username = _load_password_by_username(path)
    password_by_username[account.username] = password
    _write_password_by_username(path, password_by_username)


def delete_password(account: SmtpAccount, *, path: Path | None = None) -> None:
    """Remove the account's password from the credentials file.

    Does nothing when no password is stored, so revoking is safe to repeat.

    Raises
    ------
    CredentialsError
        The file exists but is not readable JSON, so the other accounts' entries
        cannot be preserved. Worth knowing: revoking a leaked password is exactly
        the call that ends up in a `finally`.
    """
    path = path if path is not None else default_credentials_path()
    password_by_username = _load_password_by_username(path)
    if password_by_username.pop(account.username, None) is None:
        return
    _write_password_by_username(path, password_by_username)


def _load_password_by_username(path: Path) -> dict[str, str]:
    """Read the store, or an empty one when the file does not exist yet.

    Deliberately does not police the file mode; `resolve_password` does that
    where it matters. See the note there.
    """
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise CredentialsError(f"{path} is not valid JSON: {err}") from err
    if not isinstance(stored, dict) or not all(
        isinstance(value, str) for value in stored.values()
    ):
        raise CredentialsError(
            f"{path} should map each email address to its password"
        )
    return stored


def _write_password_by_username(
    path: Path, password_by_username: dict[str, str]
) -> None:
    """Write the store: whole, or not at all, and never briefly readable.

    Written to a new file and renamed over the target, for two reasons that both
    bite the obvious implementation:

    Opening the real file with `O_TRUNC` empties it *before* the new content is
    written, so a crash in between leaves an empty store -- saving the second
    account's password would destroy the first account's. `os.replace` is atomic,
    so a reader sees either the old store or the new one.

    And `O_CREAT` applies its mode only to a file it creates; an existing store
    keeps whatever mode it had, so writing into one that had been loosened to
    0644 would put the password on disk world-readable and only tighten it
    afterwards. A fresh file is 0600 from the moment it exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CREDENTIALS_FILE_MODE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as store:
            json.dump(password_by_username, store, indent=2, ensure_ascii=False)
            store.write("\n")
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _check_owner_only_readable(path: Path) -> None:
    if not (path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        return
    raise InsecureCredentialsError(
        f"{path} is readable by more than its owner; passwords must not be. "
        f"Fix it with: chmod 600 {path}"
    )
