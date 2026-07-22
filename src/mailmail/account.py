"""The mailbox mail is sent as.

An account is the pairing of an address with the service that carries it. Where
its password lives is a separate concern -- see `credentials`.
"""

from dataclasses import dataclass

from mailmail.provider import MailProvider

__all__ = ["SmtpAccount"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SmtpAccount:
    """A mailbox to send from: the address, and the service that carries it.

    Attributes
    ----------
    name
        The key this account has in the configuration (`"naver"`, `"gmail"`).
        Distinct from `provider.name`: two accounts may share one provider.
    username
        The full email address, used both to authenticate and as `From`.
    provider
        Where to connect, and what the service will carry.
    """

    name: str
    username: str
    provider: MailProvider
