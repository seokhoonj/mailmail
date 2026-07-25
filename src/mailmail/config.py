"""The machine-local config directory, and reading the accounts and the
address book off disk.

`config_dir()` resolves the one directory mailmail keeps on the machine, by hand
from the env var the XDG spec names -- no `platformdirs` dependency, matching the
zero-dependency rule. Both files hang off it: `config.toml` here, and the `0600`
`credentials.json` read by `credentials` (which imports `config_dir` from here, so
the base is resolved in exactly one place). There is no data or state directory --
mailmail sends and forgets, writing nothing durable to relocate.

The directory lives outside any checkout because a mail setup is a property of the
machine, not of a project -- and because a project directory is exactly the kind of
place that gets synced to a cloud drive or committed by accident. Secrets are not in
this file; they are in `credentials`. What lives here is the non-secret half: the
accounts and the address book.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from mailmail.account import SmtpAccount
from mailmail.contacts import AddressBook
from mailmail.errors import ConfigError, UnknownAccountError
from mailmail.provider import resolve_provider

__all__ = [
    "STARTER_CONFIG",
    "Config",
    "config_dir",
    "default_config_path",
    "load_config",
]

CONFIG_PATH_ENV_VAR = "MAILMAIL_CONFIG"

# A ready-to-edit configuration, printed by `mailmail setup` and shown in the
# README. It lives here, beside the parser that reads this exact shape, so the
# schema has one home: a provider renamed in `provider.py` or a key changed in
# `_as_config` is a change a reader makes here too, not in a copy that drifts.
STARTER_CONFIG = """\
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "you@naver.com"

[accounts.gmail]
provider = "gmail"
username = "you@gmail.com"

[contacts]
me   = "you@naver.com"
lead = "lead@example.com"
team = ["me", "lead"]
"""


@dataclass(frozen=True, slots=True)
class Config:
    """Everything mailmail needs that is not a secret.

    Attributes
    ----------
    default_account
        Name of the account `send` uses when the caller names none.
    account_by_name
        Every configured mailbox.
    address_book
        Alias table, possibly empty.

    Both mappings are read-only, which `frozen=True` alone does not make them:
    it guards the binding while leaving the caller's `dict` writable underneath,
    and `Mailer` resolves every account through this one. The annotations already
    read as read-only and now are.
    """

    default_account: str
    account_by_name: Mapping[str, SmtpAccount]
    address_book: AddressBook

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_by_name", MappingProxyType(dict(self.account_by_name))
        )
        object.__setattr__(
            self, "address_book", MappingProxyType(dict(self.address_book))
        )

    def resolve_account(self, name: str | None = None) -> SmtpAccount:
        """Look up an account by name, or the default when `name` is None.

        Raises
        ------
        UnknownAccountError
        """
        wanted = self.default_account if name is None else name
        try:
            return self.account_by_name[wanted]
        except KeyError as err:
            known = ", ".join(sorted(self.account_by_name))
            raise UnknownAccountError(
                f"no account named {wanted!r} in the configuration; it has: {known}"
            ) from err


def config_dir() -> Path:
    """mailmail's directory on the machine: `config.toml` and the `0600`
    `credentials.json`.

    `$XDG_CONFIG_HOME/mailmail` when that variable holds an absolute path, else
    `~/.config/mailmail` -- the same on every OS (the git / ssh / aws convention),
    not a platform-native dir. A blank, whitespace-only, or *relative*
    `XDG_CONFIG_HOME` is ignored, per the XDG spec ("a relative path ... must be
    ignored"): a relative value resolves against the working directory, so a cron
    run (cwd `/`) and an interactive run (cwd `~`) would otherwise find the config
    in different places. A leading `~` is expanded first, so `~/config` is honored
    once it resolves to an absolute path; a value still relative after expansion --
    including a `~user` that names no such user -- is ignored, not an error. It has
    no override key of its own -- config cannot name the directory the config file
    itself lives in; a caller override is per-file (`MAILMAIL_CONFIG`,
    `MAILMAIL_CREDENTIALS`).

    Raises
    ------
    ConfigError
        No home directory can be found for the `~/.config` fallback (HOME is unset
        and the user has no passwd entry -- a container run as an arbitrary uid).
        Raised as a `MailmailError` rather than the bare `RuntimeError` that
        `Path.home` throws, so it stays inside the catch surface `send` documents.
    """
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if base:
        try:
            root = Path(base).expanduser()
        except RuntimeError:
            root = Path(base)  # unresolvable `~user`: stays relative, so it falls back
        if root.is_absolute():
            return root / "mailmail"
    try:
        home = Path.home()
    except RuntimeError as err:
        raise ConfigError(
            "cannot locate ~/.config/mailmail: no home directory (HOME is unset and "
            "the user has no passwd entry); set XDG_CONFIG_HOME, MAILMAIL_CONFIG, or "
            "MAILMAIL_CREDENTIALS to an absolute path"
        ) from err
    return home / ".config" / "mailmail"


def default_config_path() -> Path:
    """Where mailmail looks for its configuration.

    `MAILMAIL_CONFIG` wins; otherwise `config.toml` in `config_dir()`,
    `~/.config/mailmail/config.toml`.

    Raises
    ------
    ConfigError
        `MAILMAIL_CONFIG` names a path with an unresolvable `~user`, or no home
        directory can be found for the `~/.config` fallback (see `config_dir`).
    """
    override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if override:
        return _expand_named_path(override, source=CONFIG_PATH_ENV_VAR)
    return config_dir() / "config.toml"


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate the configuration file.

    Raises
    ------
    ConfigError
        The file is missing, cannot be read (permission or I/O error), is not
        valid UTF-8, is not valid TOML, or omits something required; or a `~user`
        cannot be resolved -- in the given path, or, when none is given, in
        `MAILMAIL_CONFIG` or the `~/.config` home fallback.
    UnknownProviderError
        An account names a provider mailmail does not know.
    """
    path = (
        _expand_named_path(path, source="the config path")
        if path is not None
        else default_config_path()
    )
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(
            f"no configuration at {path}; create it with an [accounts.<name>] "
            f"table naming a provider and a username"
        ) from err
    except OSError as err:
        raise ConfigError(f"cannot read configuration at {path}: {err}") from err
    except UnicodeDecodeError as err:
        # A ValueError, not an OSError, so it needs its own clause -- otherwise a
        # non-UTF-8 config would escape send()'s documented catch.
        raise ConfigError(f"{path} is not valid UTF-8: {err}") from err
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path} is not valid TOML: {err}") from err
    return _as_config(document, path=path)


def _expand_named_path(value: str | Path, *, source: str) -> Path:
    """Expand `~` in a caller-named path, turning an unresolvable `~user` into a
    ConfigError.

    Unlike `config_dir`, which silently ignores an unusable `XDG_CONFIG_HOME`, a
    path the caller named explicitly must not be silently dropped -- a typo should
    surface, not be swallowed. A `~user` that names nobody makes `Path.expanduser`
    raise RuntimeError, which would bypass the ConfigError catch surface `send`
    documents; convert it.
    """
    try:
        return Path(value).expanduser()
    except RuntimeError as err:
        raise ConfigError(f"{source} {value!r} names no home directory") from err


def _as_config(document: dict[str, Any], *, path: Path) -> Config:
    accounts = document.get("accounts")
    if not isinstance(accounts, dict) or not accounts:
        raise ConfigError(f"{path} defines no accounts; add an [accounts.<name>] table")
    account_by_name = {
        name: _as_account(name, table, path=path) for name, table in accounts.items()
    }
    default_account = document.get("default_account")
    if default_account is None:
        if len(account_by_name) > 1:
            known = ", ".join(sorted(account_by_name))
            raise ConfigError(
                f"{path} has several accounts ({known}) but no default_account; "
                f"name the one send should use by default"
            )
        default_account = next(iter(account_by_name))
    if default_account not in account_by_name:
        known = ", ".join(sorted(account_by_name))
        raise ConfigError(
            f"{path} sets default_account = {default_account!r}, which is not a "
            f"configured account; it has: {known}"
        )
    if "defaults" in document:
        raise ConfigError(
            f"{path} has a [defaults] table, which mailmail no longer reads. "
            f"Default recipients fired on omission, so a configured cc rode "
            f"along on every message that did not mention one -- including the "
            f"note meant for one person. Name recipients on the call instead "
            f"(`send(to=..., cc=...)`) and delete the table."
        )
    return Config(
        default_account = default_account,
        account_by_name = account_by_name,
        address_book    = _as_address_book(document.get("contacts", {}), path=path),
    )


def _as_account(name: str, table: object, *, path: Path) -> SmtpAccount:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [accounts.{name}] must be a table")
    for key in ("provider", "username"):
        if key not in table:
            raise ConfigError(f"{path}: [accounts.{name}] is missing {key!r}")
        # Checking the type, not just the presence: `username = 12345` parses
        # fine and then detonates inside the email headers, long after the config
        # file is out of view -- in a package whose whole thesis is that a bad
        # message dies at the call site.
        if not isinstance(table[key], str):
            raise ConfigError(
                f"{path}: [accounts.{name}].{key} must be a string, not "
                f"{type(table[key]).__name__}"
            )
    if "\r" in table["username"] or "\n" in table["username"]:
        # username becomes the From header; a line break would break it, or let a
        # header be injected. Refuse it here, not later inside the email machinery.
        raise ConfigError(
            f"{path}: [accounts.{name}].username has a line break"
        )
    return SmtpAccount(
        name     = name,
        username = table["username"],
        provider = resolve_provider(table["provider"]),
    )


def _as_address_book(table: object, *, path: Path) -> AddressBook:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [contacts] must be a table")
    address_book: dict[str, tuple[str, ...]] = {}
    for alias, entry in table.items():
        if isinstance(entry, str):
            address_book[alias] = (entry,)
        elif isinstance(entry, list) and all(isinstance(one, str) for one in entry):
            address_book[alias] = tuple(entry)
        elif isinstance(entry, dict):
            raise ConfigError(_dotted_key_advice(alias, entry, path=path))
        else:
            raise ConfigError(
                f"{path}: contact {alias!r} must be a string or a list of "
                f"strings, not {type(entry).__name__}"
            )
    return address_book


def _dotted_key_advice(alias: str, nested: dict[str, Any], *, path: Path) -> str:
    """Explain the dotted-key trap rather than just reporting the wrong type.

    An alias with a dot in it -- `jane.doe = "..."` -- is not a key named
    "jane.doe": TOML reads an unquoted dot as nesting, so it becomes a table
    `jane` containing `doe`. The reader sees a name they never wrote, so
    "contact 'jane' must be a string" is true but useless on its own.
    """
    # `[contacts.jane]` on its own nests an *empty* table, so there is no inner
    # key to name -- and this is the one function whose whole job is to make the
    # typo legible, which it cannot do by raising StopIteration at the reader.
    wanted = f"{alias}.{next(iter(nested), '<key>')}"
    held = ", ".join(map(repr, nested)) or "a nested table"
    return (
        f"{path}: contact {alias!r} came out as a table. TOML reads an unquoted "
        f"dot as nesting, so `{wanted} = ...` defines {alias!r} containing "
        f"{held} rather than an alias named {wanted!r}. "
        f'Quote it to keep the dot: "{wanted}" = "someone@example.com"'
    )
