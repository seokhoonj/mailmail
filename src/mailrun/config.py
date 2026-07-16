"""Reading the accounts and the address book off disk.

The file lives under the XDG config directory rather than beside the code,
because a mail setup is a property of the machine, not of a checkout -- and
because a project directory is exactly the kind of place that gets synced to a
cloud drive or committed by accident.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mailrun.account import SmtpAccount
from mailrun.contacts import AddressBook
from mailrun.errors import ConfigError, UnknownAccountError
from mailrun.provider import resolve_provider

__all__ = ["Config", "default_config_path", "load_config"]

CONFIG_PATH_ENV_VAR = "MAILRUN_CONFIG"


@dataclass(frozen=True, slots=True)
class Config:
    """Everything mailrun needs that is not a secret.

    Attributes
    ----------
    default_account
        Name of the account `send_mail` uses when the caller names none.
    account_by_name
        Every configured mailbox.
    address_book
        Alias table, possibly empty.
    default_to, default_cc, default_bcc
        Recipients for a message that names none. Addresses or aliases, resolved
        the same way an explicit argument is.

        These fire on *omission*, which makes them worth thinking about twice: a
        default cc means every message that does not mention cc carries it,
        including the one-line note you meant only for yourself. `send_mail`
        distinguishes "not mentioned" (`None`, take the default) from "nobody"
        (`()`, send to nobody), so the escape hatch is always one argument away.
    """

    default_account: str
    account_by_name: Mapping[str, SmtpAccount]
    address_book: AddressBook
    default_to: tuple[str, ...] = ()
    default_cc: tuple[str, ...] = ()
    default_bcc: tuple[str, ...] = ()

    def account(self, name: str | None = None) -> SmtpAccount:
        """Look up an account by name, or the default when `name` is None.

        Raises
        ------
        UnknownAccountError
        """
        wanted = name or self.default_account
        try:
            return self.account_by_name[wanted]
        except KeyError as err:
            known = ", ".join(sorted(self.account_by_name))
            raise UnknownAccountError(
                f"no account named {wanted!r} in the configuration; it has: {known}"
            ) from err


def default_config_path() -> Path:
    """Where mailrun looks for its configuration.

    `MAILRUN_CONFIG` wins; otherwise the XDG location,
    `~/.config/mailrun/config.toml`.
    """
    override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser()
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return config_home / "mailrun" / "config.toml"


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate the configuration file.

    Raises
    ------
    ConfigError
        The file is missing, is not valid TOML, or omits something required.
    UnknownProviderError
        An account names a provider mailrun does not know.
    """
    path = Path(path).expanduser() if path is not None else default_config_path()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(
            f"no configuration at {path}; create it with an [accounts.<name>] "
            f"table naming a provider and a username"
        ) from err
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path} is not valid TOML: {err}") from err
    return _as_config(document, path=path)


def _as_config(document: dict[str, Any], *, path: Path) -> Config:
    accounts = document.get("accounts")
    if not accounts:
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
                f"name the one send_mail should use by default"
            )
        default_account = next(iter(account_by_name))
    if default_account not in account_by_name:
        known = ", ".join(sorted(account_by_name))
        raise ConfigError(
            f"{path} sets default_account = {default_account!r}, which is not a "
            f"configured account; it has: {known}"
        )
    defaults = document.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError(f"{path}: [defaults] must be a table")
    return Config(
        default_account = default_account,
        account_by_name = account_by_name,
        address_book    = _as_address_book(document.get("contacts", {}), path=path),
        default_to      = _as_recipients(defaults, "to", path=path),
        default_cc      = _as_recipients(defaults, "cc", path=path),
        default_bcc     = _as_recipients(defaults, "bcc", path=path),
    )


def _as_recipients(
    defaults: dict[str, Any], key: str, *, path: Path
) -> tuple[str, ...]:
    entry = defaults.get(key)
    if entry is None:
        return ()
    if isinstance(entry, str):
        return (entry,)
    if isinstance(entry, list) and all(isinstance(one, str) for one in entry):
        return tuple(entry)
    raise ConfigError(
        f"{path}: defaults.{key} must be a string or a list of strings, "
        f"not {type(entry).__name__}"
    )


def _as_account(name: str, table: Any, *, path: Path) -> SmtpAccount:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [accounts.{name}] must be a table")
    for key in ("provider", "username"):
        if key not in table:
            raise ConfigError(f"{path}: [accounts.{name}] is missing {key!r}")
    return SmtpAccount(
        name     = name,
        username = table["username"],
        provider = resolve_provider(table["provider"]),
    )


def _as_address_book(table: Any, *, path: Path) -> AddressBook:
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [contacts] must be a table")
    address_book: dict[str, tuple[str, ...]] = {}
    for alias, entry in table.items():
        if isinstance(entry, str):
            address_book[alias] = (entry,)
        elif isinstance(entry, list) and all(isinstance(one, str) for one in entry):
            address_book[alias] = tuple(entry)
        elif isinstance(entry, dict):
            raise ConfigError(_as_dotted_key_advice(alias, entry, path=path))
        else:
            raise ConfigError(
                f"{path}: contact {alias!r} must be a string or a list of "
                f"strings, not {type(entry).__name__}"
            )
    return address_book


def _as_dotted_key_advice(alias: str, nested: dict[str, Any], *, path: Path) -> str:
    """Explain the dotted-key trap rather than just reporting the wrong type.

    An alias with a dot in it -- `jane.doe = "..."` -- is not a key named
    "jane.doe": TOML reads an unquoted dot as nesting, so it becomes a table
    `jane` containing `doe`. The reader sees a name they never wrote, so
    "contact 'jane' must be a string" is true but useless on its own.
    """
    wanted = f"{alias}.{next(iter(nested))}"
    return (
        f"{path}: contact {alias!r} came out as a table. TOML reads an unquoted "
        f"dot as nesting, so `{wanted} = ...` defines {alias!r} containing "
        f"{', '.join(map(repr, nested))} rather than an alias named {wanted!r}. "
        f'Quote it to keep the dot: "{wanted}" = "someone@example.com"'
    )
