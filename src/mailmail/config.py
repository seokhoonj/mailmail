"""The machine-local config directory, and reading and writing the accounts and
the address book on disk.

`config_dir()` resolves the directory `config.toml` lives in through credbox's own
`config_dir`, the same resolver credbox uses for the `0600` `credentials.json` beside
it -- so by default, and on every platform, the two files share one directory by
construction rather than by two resolvers agreeing. The one override that parts them is
deliberate: `MAILMAIL_STORE_APP` redirects only the *store* (to fold a mail setup into a
host program's shared credentials), leaving this non-secret config where it is. There is
no data or state directory -- mailmail sends and forgets, writing nothing durable to
relocate.

The directory lives outside any checkout because a mail setup is a property of the
machine, not of a project -- and because a project directory is exactly the kind of
place that gets synced to a cloud drive or committed by accident. Secrets are not in
this file; they are in `credentials`. What lives here is the non-secret half: the
accounts and the address book.
"""

import os
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from credbox import CredBoxError, colliding_env_var_prefixes
from credbox import config_dir as credbox_config_dir
from tomlite import TOMLEditor, TomliteError, TOMLValue

from mailmail.account import SmtpAccount
from mailmail.contacts import AddressBook
from mailmail.credentials import PASSWORD_ENV_VAR
from mailmail.errors import ConfigError, InvalidAddressError, UnknownAccountError
from mailmail.provider import MailProvider, provider_for_email, resolve_provider

__all__ = [
    "STARTER_CONFIG",
    "Config",
    "account_for",
    "add_account",
    "add_contact",
    "add_group",
    "config_dir",
    "default_config_path",
    "import_contacts",
    "load_config",
]

CONFIG_PATH_ENV_VAR = "MAILMAIL_CONFIG"

# A ready-to-edit configuration, printed by `mailmail setup` and shown in the
# README. It lives here, beside the parser that reads this exact shape, so the
# schema has one home: a key changed in `_as_config` is a change a reader makes
# here too, not in a copy that drifts. `set-password`, `add-contact`, and
# `add-group` write this same shape, so most users never edit it by hand.
STARTER_CONFIG = """\
default_account = "personal"

[[accounts]]
email = "you@naver.com"
alias = "personal"

[[accounts]]
email = "you@gmail.com"
alias = "work"

[contacts]
manager = "manager@example.com"
lead    = "lead@example.com"
friend  = "friend@example.com"
team    = ["manager", "lead"]
"""


@dataclass(frozen=True, slots=True)
class Config:
    """Everything mailmail needs that is not a secret.

    Attributes
    ----------
    default_account
        Handle of the account `send` uses when the caller names none.
    account_by_handle
        Every configured mailbox, keyed by its handle (alias, or address when it
        has no alias).
    address_book
        Alias table, possibly empty.

    Both mappings are read-only, which `frozen=True` alone does not make them:
    it guards the binding while leaving the caller's `dict` writable underneath,
    and `Mailer` resolves every account through this one. The annotations already
    read as read-only and now are.
    """

    default_account: str
    account_by_handle: Mapping[str, SmtpAccount]
    address_book: AddressBook

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_by_handle", MappingProxyType(dict(self.account_by_handle))
        )
        object.__setattr__(
            self, "address_book", MappingProxyType(dict(self.address_book))
        )

    def resolve_account(self, handle: str | None = None) -> SmtpAccount:
        """Look up an account by handle, or the default when `handle` is None.

        Raises
        ------
        UnknownAccountError
        """
        wanted = self.default_account if handle is None else handle
        try:
            return self.account_by_handle[wanted]
        except KeyError as err:
            known = ", ".join(sorted(self.account_by_handle))
            raise UnknownAccountError(
                f"no account named {wanted!r} in the configuration; it has: {known}"
            ) from err


def config_dir() -> Path:
    """mailmail's directory on the machine: `config.toml` and the `0600`
    `credentials.json` beside it.

    Delegated to credbox's `config_dir`, so the config this module writes and the
    credential store credbox writes resolve to the *same* directory -- co-located by
    construction under whatever layout credbox is on -- instead of two hand-rolled
    resolvers that could drift apart. By default that is `$XDG_CONFIG_HOME/mailmail`
    when that holds an absolute path, else `~/.config/mailmail` (the git / ssh / aws
    convention; a relative, blank, or unresolvable `XDG_CONFIG_HOME` is ignored per the
    XDG spec). It follows credbox's layout, so a machine or host that puts credbox on
    the native layout (`CREDBOX_LAYOUT=native`) moves the config to the OS-native dir --
    but the store moves with it, so the two stay together. `MAILMAIL_CONFIG_DIR` is an
    absolute-path override credbox honors for both files at once; the config *file*
    keeps its own per-file override, `MAILMAIL_CONFIG` (see `default_config_path`).

    Raises
    ------
    ConfigError
        No home directory can be found for the `~/.config` fallback (HOME is unset
        and the user has no passwd entry -- a container run as an arbitrary uid).
        The store's own error is translated to `ConfigError` so it stays inside the
        `MailmailError` catch surface `send` documents.
    """
    try:
        return credbox_config_dir("mailmail")
    except CredBoxError as err:
        raise ConfigError(
            f"cannot locate the mailmail config directory: {err}"
        ) from err


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
    path = _resolve_path(path)
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ConfigError(
            f"no configuration at {path}; set up an account with "
            f"`mailmail set-password <you@naver.com>`"
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


def account_for(email: str, *, alias: str | None = None) -> SmtpAccount:
    """Build the account `email` (with optional `alias`) names, inferring its provider
    from the domain -- without touching any file. Used to validate and resolve a mailbox
    before prompting for a password, so a typo or an unsupported domain fails first.
    `alias` is keyword-only so it cannot be swapped with the address.

    Raises
    ------
    InvalidAddressError
        `email` is not a well-formed address, or `alias` is address-shaped or blank.
    UnknownProviderError
        The address's domain is not one mailmail sends through.
    """
    _validate_email(email)
    _validate_alias(alias)
    return SmtpAccount(email=email, alias=alias, provider=provider_for_email(email))


def add_account(account: SmtpAccount, *, path: Path | str | None = None) -> None:
    """Write `account` into the configuration, creating the file if needed and leaving
    every other entry -- comments included -- as it was. A new address is appended as an
    `[[accounts]]` block (above `[contacts]` when present); an address already present
    only has its alias updated. The first account written becomes `default_account`.

    Raises
    ------
    ConfigError
        The configuration path cannot be resolved (see `config_dir`), or the file could
        not be written.
    """
    with _editing_config(path) as document:
        _refuse_legacy_accounts(document, path=_resolve_path(path))
        if not document.has_in_array(
            "accounts", match_field="email", match_value=account.email
        ):
            fields: list[tuple[str, TOMLValue]] = [("email", account.email)]
            if account.alias is not None:
                fields.append(("alias", account.alias))
            document.append_to_array("accounts", fields, before_table="contacts")
        elif account.alias is not None:
            document.update_in_array(
                "accounts", match_field="email", match_value=account.email,
                field="alias", value=account.alias,
            )
        document.set_root_key("default_account", account.handle, only_if_absent=True)


def add_contact(name: str, address: str, *, path: Path | str | None = None) -> None:
    """Add or update address-book entry `name`, pointing at a single `address`. Comments
    and other entries are left untouched.

    Raises
    ------
    InvalidAddressError
        `address` is not a well-formed email, or `name` looks like an address (an `@`
        would make it read as a recipient rather than an alias).
    ConfigError
        The configuration path cannot be resolved, or the file could not be written.
    """
    _validate_contact_name(name)
    _validate_email(address)
    with _editing_config(path) as document:
        document.set_table_key("contacts", name, address)


def add_group(
    name: str, members: Sequence[str], *, path: Path | str | None = None
) -> None:
    """Add or update address-book group `name`, standing for `members`. A member with an
    `@` is a literal address and is validated as one now; a bare member is an alias,
    resolved at send time (so it need not exist yet). Comments and other entries are
    left untouched.

    Raises
    ------
    InvalidAddressError
        `name` looks like an address, or a member that carries an `@` is not a
        well-formed email.
    ConfigError
        `members` is empty, the path cannot be resolved, or the file could not be
        written.
    """
    _validate_contact_name(name)
    if not members:
        raise ConfigError(f"group {name!r} needs at least one member")
    for member in members:
        if "@" in member:
            _validate_email(member)
    with _editing_config(path) as document:
        document.set_table_key("contacts", name, tuple(members))


def import_contacts(
    contacts: Sequence[tuple[str, str]], *, path: Path | str | None = None
) -> int:
    """Add or update many address-book contacts at once, each a `name` pointing at a
    single `address`, and return how many were written. Every pair is validated first;
    only if all pass is the file written -- one atomic write -- so a bad row leaves the
    file untouched rather than half-imported. Comments and other entries are preserved,
    and a name already in the book has its address replaced. Used for a bulk load where
    running `add_contact` once per row would rewrite the file once per row.

    Raises
    ------
    InvalidAddressError
        A name looks like an address (an `@`) or is blank, or an address is not a
        well-formed email. Raised before anything is written, so nothing is stored.
    ConfigError
        `contacts` is empty or names one contact twice, the path cannot be resolved, or
        the file could not be read or written.
    """
    if not contacts:
        raise ConfigError("no contacts to import")
    seen: set[str] = set()
    for name, address in contacts:
        _validate_contact_name(name)
        _validate_email(address)
        if name in seen:
            # A repeated name would silently keep only the last address and make the
            # returned count overstate what was written -- refuse it, as the config
            # refuses two accounts sharing a handle, rather than guess which was meant.
            raise ConfigError(f"contact {name!r} is named more than once in the import")
        seen.add(name)
    with _editing_config(path) as document:
        for name, address in contacts:
            document.set_table_key("contacts", name, address)
    return len(contacts)


def _load_document(target: Path) -> TOMLEditor:
    """Read the config for editing, turning a read failure into a ConfigError (a missing
    file is not one -- it yields an empty document to write into).

    tomlite refuses a file that is not valid TOML at two different moments: a bad
    *encoding* (not UTF-8, as a cp949 spreadsheet export is) is refused here at load,
    while an unsupported *grammar* (a dotted key, a multi-line string) is refused at the
    first edit and caught by `_editing`. Both are `TomliteError`; translate the
    load-time one so a non-UTF-8 config fails the write path with the same ConfigError
    the read path (`load_config`) already gives it, not a foreign exception past the
    `MailmailError` surface the CLI catches."""
    try:
        return TOMLEditor.load(target)
    except OSError as err:
        raise ConfigError(f"cannot read configuration at {target}: {err}") from err
    except TomliteError as err:
        # tomlite's message already names the path, so don't prefix it again.
        raise ConfigError(f"cannot read configuration: {err}") from err


@contextmanager
def _editing_config(path: Path | str | None) -> Iterator[TOMLEditor]:
    """Open the config for a setup command and save it afterward -- the resolve -> load
    -> edit -> write lifecycle every setup command shares, in one place.

    Resolve the path, load the document (a read failure -- an I/O error, or a non-UTF-8
    file tomlite refuses at load -- becomes ConfigError in `_load_document`), yield it
    for edits, then write it back. tomlite refuses rather than corrupts and defers a
    *grammar* refusal to the first edit: an existing file it cannot edit safely (a
    dotted key), or
    an edit that would make the file invalid, raises `TomliteError` here, which is
    translated to ConfigError so a setup command reports one clean line inside the
    `MailmailError` surface -- and, because the write is then skipped, a refused edit
    leaves the file untouched.
    """
    target = _resolve_path(path)
    document = _load_document(target)
    try:
        yield document
    except TomliteError as err:
        raise ConfigError(
            f"cannot update the configuration at {target}: {err}"
        ) from err
    _write(document, target)


def _write(document: TOMLEditor, target: Path) -> None:
    """Persist edits, turning a disk failure into a ConfigError so the write path's
    contract matches the read path's, which wraps its OSError the same way."""
    try:
        document.save(target)
    except OSError as err:
        raise ConfigError(f"cannot write configuration at {target}: {err}") from err


def _refuse_legacy_accounts(document: TOMLEditor, *, path: Path) -> None:
    """Refuse to edit accounts in a config still written in the old `[accounts.<name>]`
    table layout, with advice, before tomlite refuses it with a message about arrays and
    tables that means nothing to someone editing a mail config.

    `load_config` still *reads* that layout, so a config written before the switch keeps
    sending; but the setup commands only write the `[[accounts]]` array form, and an
    array-of-tables cannot share the name `accounts` with a table -- TOML has no such
    shape, so appending one would be refused anyway. Detected from the parsed document
    (tomlite exposes lines, not the table/array shape) so the message can name the fix.
    """
    try:
        current = tomllib.loads(document.dumps())
    except tomllib.TOMLDecodeError:
        return  # a file even tomllib rejects: let the edit's own refusal report it
    if isinstance(current.get("accounts"), dict):
        raise ConfigError(
            f"{path} uses the old [accounts.<name>] layout, which the setup "
            f"commands no longer edit -- they write [[accounts]] blocks, and TOML "
            f"cannot hold both under the name 'accounts'. Rewrite the accounts as "
            f"[[accounts]] entries (`mailmail setup` prints the shape) or start a "
            f"fresh config; sending still reads the old layout in the meantime."
        )


def _validate_email(value: str) -> None:
    """Raise unless `value` is a plausibly well-formed address: one `@`, a non-empty
    local part and domain, a dot in the domain, and no whitespace, control character,
    or TOML-structural character (`[ ] " \\`). A deliberately light check -- enough to
    catch a typo at entry, not a full RFC parser (the send path is where an address
    must truly parse)."""
    local, at, domain = value.partition("@")
    unsafe = any(ch <= " " or ch == "\x7f" or ch in '[]"\\' for ch in value)
    if (
        not at or not local or not domain
        or "@" in domain or "." not in domain or unsafe
    ):
        raise InvalidAddressError(f"{value!r} is not a valid email address")


def _validate_alias(alias: str | None) -> None:
    """Raise unless `alias` is usable as a handle -- the credential-store key and
    `default_account`. When given it must be non-empty and free of `@` (an
    address-shaped handle is confusing) and line breaks (it would break the store key).
    `None` is fine: the account then goes by its address."""
    if alias is not None and (
        not alias or "@" in alias or "\r" in alias or "\n" in alias
    ):
        raise InvalidAddressError(
            f"alias {alias!r} must be a plain handle, not an address (no '@') or blank"
        )


def _validate_contact_name(name: str) -> None:
    """Raise unless `name` is usable as an address-book alias: non-empty, no `@` (that
    is how an address is told from an alias), no line break."""
    if not name or "@" in name or "\r" in name or "\n" in name:
        raise InvalidAddressError(
            f"contact name {name!r} must be a plain alias, not an address (no '@')"
        )


def _resolve_path(path: Path | str | None) -> Path:
    """The config path to read or write: the one given (with `~` expanded), else the
    default location. Shared by `load_config` and the write commands so both agree on
    where the configuration lives."""
    if path is None:
        return default_config_path()
    return _expand_named_path(path, source="the config path")


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
    accounts = _as_accounts(document.get("accounts"), path=path)
    account_by_handle: dict[str, SmtpAccount] = {}
    for account in accounts:
        if account.handle in account_by_handle:
            raise ConfigError(
                f"{path}: two accounts share the handle {account.handle!r}; "
                f"give one a distinct alias"
            )
        account_by_handle[account.handle] = account
    collisions = colliding_env_var_prefixes(account_by_handle)
    if collisions:
        # Handles that differ only in punctuation fold to one password env var
        # (`me-naver` and `me.naver` both -> MAILMAIL_PASSWORD_ME_NAVER), so an
        # exported password could not say which account it is for -- the very
        # disclosure the per-account name exists to prevent. Refuse the config, as
        # the shared-handle check just above does, rather than resolve it silently.
        prefix, names = min(collisions.items())
        raise ConfigError(
            f"{path}: accounts {' and '.join(map(repr, names))} both fold to the "
            f"password environment variable {PASSWORD_ENV_VAR}_{prefix}, so an "
            f"exported password could not tell them apart; give them handles that "
            f"differ by a letter or digit, not just '.', '-', or '_'"
        )
    default_account = document.get("default_account")
    if default_account is not None and not isinstance(default_account, str):
        # A raw read: `default_account = ["x"]` or `= {a=1}` would otherwise reach the
        # `not in account_by_handle` test below and raise TypeError (unhashable),
        # escaping send()'s documented catch surface.
        raise ConfigError(
            f"{path}: default_account must be a string, not "
            f"{type(default_account).__name__}"
        )
    if default_account is None:
        if len(account_by_handle) > 1:
            known = ", ".join(sorted(account_by_handle))
            raise ConfigError(
                f"{path} has several accounts ({known}) but no default_account; "
                f"name the one send should use by default"
            )
        default_account = next(iter(account_by_handle))
    if default_account not in account_by_handle:
        known = ", ".join(sorted(account_by_handle))
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
        default_account   = default_account,
        account_by_handle = account_by_handle,
        address_book      = _as_address_book(document.get("contacts", {}), path=path),
    )


def _as_accounts(raw: object, *, path: Path) -> list[SmtpAccount]:
    """The configured mailboxes, from either the current `[[accounts]]` array or the
    legacy `[accounts.<name>]` tables. Reading both lets a config written before this
    layout keep working untouched; the writer only ever emits the array form."""
    if isinstance(raw, list) and raw:
        return [_as_account_table(item, path=path) for item in raw]
    if isinstance(raw, dict) and raw:
        return [
            _as_legacy_account(name, table, path=path) for name, table in raw.items()
        ]
    raise ConfigError(
        f"{path} defines no accounts; set one up with "
        f"`mailmail set-password <you@naver.com>`"
    )


def _as_account_table(item: object, *, path: Path) -> SmtpAccount:
    """One `[[accounts]]` entry: a required string `email`, an optional `alias`, and an
    optional `provider` that overrides domain inference (for a known provider on an
    off-list domain)."""
    if not isinstance(item, dict):
        raise ConfigError(f"{path}: each [[accounts]] entry must be a table")
    email = _account_string(item, "email", path=path, label="[[accounts]]")
    alias = item.get("alias")
    if alias is not None and not isinstance(alias, str):
        raise ConfigError(
            f"{path}: [[accounts]].alias must be a string, not "
            f"{type(alias).__name__}"
        )
    return SmtpAccount(
        email=email, alias=alias,
        provider=_provider_of(item, email, path=path, label="[[accounts]]"),
    )


def _as_legacy_account(name: str, table: object, *, path: Path) -> SmtpAccount:
    """One legacy `[accounts.<name>]` table: the table key becomes the alias, its
    `username` the address, and an explicit `provider` is honored (else inferred)."""
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [accounts.{name}] must be a table")
    email = _account_string(table, "username", path=path, label=f"[accounts.{name}]")
    return SmtpAccount(
        email=email, alias=name,
        provider=_provider_of(table, email, path=path, label=f"[accounts.{name}]"),
    )


def _account_string(
    table: dict[str, Any], key: str, *, path: Path, label: str
) -> str:
    """A required string field of an account table, checked for type and line breaks --
    the address becomes the `From` header, where a wrong type detonates far from the
    config and a line break would let a header be injected."""
    if key not in table:
        raise ConfigError(f"{path}: {label} is missing {key!r}")
    value = table[key]
    if not isinstance(value, str):
        raise ConfigError(
            f"{path}: {label}.{key} must be a string, not {type(value).__name__}"
        )
    if "\r" in value or "\n" in value:
        raise ConfigError(f"{path}: {label}.{key} has a line break")
    return value


def _provider_of(
    table: dict[str, Any], email: str, *, path: Path, label: str
) -> MailProvider:
    """The account's provider: the explicit `provider` key when present (a known
    provider on an off-list domain), else inferred from the address's domain. A
    present-but-non-string `provider` is refused, not silently ignored -- the same
    boundary check the sibling `email`/`alias` fields get."""
    named = table.get("provider")
    if named is None:
        return provider_for_email(email)
    if not isinstance(named, str):
        raise ConfigError(
            f"{path}: {label}.provider must be a string, not {type(named).__name__}"
        )
    return resolve_provider(named)


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
