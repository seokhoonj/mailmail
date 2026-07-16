"""Exceptions raised by mailrun.

Every error the package raises descends from `MailrunError`, so a caller can
guard a whole send with one `except` without catching unrelated failures.
"""

__all__ = [
    "AttachmentError",
    "AuthenticationFailedError",
    "BlockedAttachmentError",
    "ConfigError",
    "ContactCycleError",
    "ContactError",
    "CredentialsError",
    "EncryptedArchiveError",
    "InsecureCredentialsError",
    "InvalidMessageError",
    "MailrunError",
    "MessageTooLargeError",
    "MissingPasswordError",
    "RecipientRefusedError",
    "UnknownAccountError",
    "UnknownContactError",
    "UnknownProviderError",
]


class MailrunError(Exception):
    """Base class for every error mailrun raises."""


class ConfigError(MailrunError):
    """The configuration file is missing, malformed, or incomplete."""


class UnknownAccountError(ConfigError):
    """An account name was requested that the configuration does not define."""


class UnknownProviderError(ConfigError):
    """An account names a mail provider mailrun does not know how to reach."""


class ContactError(MailrunError):
    """Base class for address-book entries that cannot be resolved."""


class UnknownContactError(ContactError):
    """A recipient is neither an email address nor a known address-book alias."""


class ContactCycleError(ContactError):
    """Address-book aliases refer to each other in a loop."""


class InvalidMessageError(MailrunError, ValueError):
    """The message is not sendable as given: no recipient, or no subject.

    Also a `ValueError`, which is what a bad argument has always been in Python
    and what callers already catch. Inheriting both keeps the promise at the top
    of this file true -- that one `except MailrunError` guards a whole send --
    without breaking anyone who reasonably wrote `except ValueError`.
    """


class AttachmentError(MailrunError):
    """Base class for attachments the provider would reject."""


class BlockedAttachmentError(AttachmentError):
    """The provider blocks this file type, so the send would bounce."""


class EncryptedArchiveError(AttachmentError):
    """The archive is password-protected; providers reject what they cannot scan."""


class MessageTooLargeError(MailrunError):
    """The encoded message exceeds what the provider's SMTP server accepts."""


class CredentialsError(MailrunError):
    """Base class for problems with the stored passwords."""


class MissingPasswordError(CredentialsError):
    """No password is stored for the account, so authentication cannot proceed."""


class InsecureCredentialsError(CredentialsError):
    """The credentials file is readable by someone other than its owner."""


class AuthenticationFailedError(CredentialsError):
    """The server rejected the account's password.

    Carries the provider's own setup requirements, because "wrong password" is
    almost never the real story: both Gmail and Naver reject the account login
    password outright and want an app password instead.
    """


class RecipientRefusedError(MailrunError):
    """The server refused every recipient, so nothing was delivered."""
