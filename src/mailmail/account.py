"""The mailbox mail is sent as.

An account is an address plus the service that carries it. The service is not stored --
it is inferred from the address's domain (see `provider.provider_for_email`) -- so an
account is set up from its address alone. Where its password lives is a separate
concern; see `credentials`.
"""

from dataclasses import dataclass

from mailmail.provider import MailProvider

__all__ = ["SmtpAccount"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SmtpAccount:
    """A mailbox to send from: the address, an optional short handle, and the service.

    Attributes
    ----------
    email
        The full email address, used both to authenticate and as `From`.
    alias
        An optional short handle for the account (`"me-naver"`), so the command line
        and the credential store can name it something friendlier than the address.
        `None` when the account goes by its address.
    provider
        Where to connect, and what the service will carry. Derived from `email`'s
        domain when the configuration does not name one.

    handle
        How the account is referred to everywhere else -- `--account`, the credential
        key, `default_account`: the alias when it has one, otherwise the address. Two
        accounts must not share a handle.
    """

    email: str
    alias: str | None
    provider: MailProvider

    @property
    def handle(self) -> str:
        return self.alias or self.email
