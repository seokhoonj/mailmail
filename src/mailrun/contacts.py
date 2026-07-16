"""Turning address-book aliases into email addresses.

An entry is an address when it contains `@`, and an alias otherwise. Aliases
resolve to other entries, so a group can name its members rather than repeating
their addresses -- one place to fix when somebody's address changes.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import TypeAlias

from mailrun.errors import ContactCycleError, UnknownContactError

__all__ = ["AddressBook", "resolve_recipients"]

# alias -> the entries it stands for, each either an address or another alias.
AddressBook: TypeAlias = Mapping[str, tuple[str, ...]]


def resolve_recipients(
    recipients: str | Iterable[str], *, address_book: AddressBook
) -> list[str]:
    """Expand aliases to addresses, in order, without duplicates.

    Parameters
    ----------
    recipients
        Addresses, aliases, or a mix. A lone string counts as one recipient.
    address_book
        The alias table, normally `Config.address_book`.

    Returns
    -------
    list[str]
        Email addresses, first-seen order preserved so the reader can predict the
        `To` header from what they typed.

    Raises
    ------
    UnknownContactError
        An entry is neither an address nor a known alias.
    ContactCycleError
        Aliases refer to each other in a loop.
    """
    if isinstance(recipients, str):
        recipients = (recipients,)
    resolved: dict[str, None] = {}  # doubles as an ordered set
    for recipient in recipients:
        _resolve_into(recipient, address_book, resolved, trail=())
    return list(resolved)


def _resolve_into(
    entry: str,
    address_book: AddressBook,
    resolved: dict[str, None],
    *,
    trail: Sequence[str],
) -> None:
    if _is_address(entry):
        resolved[entry] = None
        return
    if entry in trail:
        loop = " -> ".join([*trail, entry])
        raise ContactCycleError(f"address-book aliases form a loop: {loop}")
    try:
        members = address_book[entry]
    except KeyError as err:
        known = ", ".join(sorted(address_book)) or "(the address book is empty)"
        raise UnknownContactError(
            f"{entry!r} is neither an email address nor a known alias; "
            f"the address book has: {known}"
        ) from err
    for member in members:
        _resolve_into(member, address_book, resolved, trail=(*trail, entry))


def _is_address(entry: str) -> bool:
    return "@" in entry
