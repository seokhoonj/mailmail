"""Exceptions raised by mailmail.

Every error mailmail *defines* descends from `MailmailError`, so one `except`
catches everything this package judges: a blocked attachment, an unknown alias,
a message the server would refuse, a password that is missing or badly kept.

It does not catch everything a send can raise, and used to claim it did. Two
kinds pass through untranslated, because they are the standard library's own
and inventing a wrapper for them would say less than they already do:

    smtplib.SMTPException     the session failed -- the server hung up, spoke
                              something unexpected, dropped STARTTLS
    OSError                   the network did -- DNS, refused connection,
                              timeout; `ssl.SSLCertVerificationError` when the
                              server is not who it says it is

`AuthenticationFailedError` is the one session failure that is translated, and
the exception's own docstring says why. A caller who must catch every way a send
can fail writes:

    except (MailmailError, smtplib.SMTPException, OSError)
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
    "MailmailError",
    "MessageTooLargeError",
    "MissingPasswordError",
    "RecipientRefusedError",
    "TooManyRecipientsError",
    "UnknownAccountError",
    "UnknownContactError",
    "UnknownProviderError",
    "UnscannableArchiveError",
]


class MailmailError(Exception):
    """Base class for every error mailmail raises."""


class ConfigError(MailmailError):
    """The configuration file is missing, malformed, or incomplete."""


class UnknownAccountError(ConfigError):
    """An account name was requested that the configuration does not define."""


class UnknownProviderError(ConfigError):
    """An account names a mail provider mailmail does not know how to reach."""


class ContactError(MailmailError):
    """Base class for address-book entries that cannot be resolved."""


class UnknownContactError(ContactError):
    """A recipient is neither an email address nor a known address-book alias."""


class ContactCycleError(ContactError):
    """Address-book aliases refer to each other in a loop."""


class InvalidMessageError(MailmailError, ValueError):
    """The message is not sendable as given: no recipient, or no subject.

    Also a `ValueError`, which is what a bad argument has always been in Python
    and what callers already catch. Inheriting both keeps the promise at the top
    of this file true -- that one `except MailmailError` guards a whole send --
    without breaking anyone who reasonably wrote `except ValueError`.
    """


class AttachmentError(MailmailError):
    """A file cannot be attached, or the provider would refuse it.

    Raised directly when the path is missing, is not a regular file, names an
    unresolvable `~user`, has a name that is not valid UTF-8 or contains a line
    break, or the MIME type is malformed -- none of which a provider ever sees. It
    is also the base class for the provider's own refusals below, so one `except`
    covers both kinds.
    """


class BlockedAttachmentError(AttachmentError):
    """The provider blocks this file type, so the send would bounce."""


class UnscannableArchiveError(AttachmentError):
    """The archive cannot be looked inside, so what it holds is unknown.

    Providers reject what they cannot scan, and so does this -- "we could not
    look" must not read as "nothing in there".
    """


class EncryptedArchiveError(UnscannableArchiveError):
    """The archive is password-protected, so no scanner can open it.

    One reason among several for being unscannable, and the only one worth its
    own name: it is the one the sender can do something about.
    """


class MessageTooLargeError(MailmailError):
    """The encoded message exceeds what the provider's SMTP server accepts."""


class TooManyRecipientsError(MailmailError):
    """One message names more recipients than the provider accepts in a send.

    Gmail and Naver both cap a single SMTP message at 100 recipients (`to`, `cc`,
    and `bcc` combined). Refused here, before the connection opens, rather than as
    a server rejection partway through the envelope.
    """


class CredentialsError(MailmailError):
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


class RecipientRefusedError(MailmailError):
    """The server refused every recipient, so nothing was delivered."""
