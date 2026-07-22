"""Send mail from Python through Gmail or Naver SMTP.

    from mailmail import send_mail

    send_mail(
        to          = "lead",
        subject     = "Weekly report",
        body        = "Hi,\\n\\nThis week's report is attached.\\n\\nBest regards,\\n",
        attachments = ["report.xlsx"],
    )

`to` takes addresses, address-book aliases, or both, and is required -- nothing
is ever addressed on your behalf. Anything the provider would reject -- a blocked
file type, an archive it cannot scan, a message over the server's size limit --
raises before a connection is opened, so failures land at the call site instead
of arriving later as a bounce.

Sending as a particular mailbox is `account="gmail"`; reusing one connection for
a batch is `Mailer`.
"""

from collections.abc import Iterable
from pathlib import Path

from mailmail.account import SmtpAccount
from mailmail.attachment import Attachment
from mailmail.config import Config, default_config_path, load_config
from mailmail.contacts import AddressBook, resolve_recipients
from mailmail.credentials import (
    PASSWORD_ENV_VAR,
    default_credentials_path,
    delete_password,
    resolve_password,
    store_password,
)
from mailmail.errors import (
    AttachmentError,
    AuthenticationFailedError,
    BlockedAttachmentError,
    ConfigError,
    ContactCycleError,
    ContactError,
    CredentialsError,
    EncryptedArchiveError,
    InsecureCredentialsError,
    InvalidMessageError,
    MailmailError,
    MessageTooLargeError,
    MissingPasswordError,
    RecipientRefusedError,
    UnknownAccountError,
    UnknownContactError,
    UnknownProviderError,
    UnscannableArchiveError,
)
from mailmail.mailer import Mailer, SendReceipt
from mailmail.message import Message
from mailmail.provider import GMAIL, NAVER, MailProvider, SmtpSecurity

__all__ = [
    "GMAIL",
    "NAVER",
    "PASSWORD_ENV_VAR",
    "AddressBook",
    "Attachment",
    "AttachmentError",
    "AuthenticationFailedError",
    "BlockedAttachmentError",
    "Config",
    "ConfigError",
    "ContactCycleError",
    "ContactError",
    "CredentialsError",
    "EncryptedArchiveError",
    "InsecureCredentialsError",
    "InvalidMessageError",
    "MailProvider",
    "Mailer",
    "MailmailError",
    "Message",
    "MessageTooLargeError",
    "MissingPasswordError",
    "RecipientRefusedError",
    "SendReceipt",
    "SmtpAccount",
    "SmtpSecurity",
    "UnknownAccountError",
    "UnknownContactError",
    "UnknownProviderError",
    "UnscannableArchiveError",
    "default_config_path",
    "default_credentials_path",
    "delete_password",
    "load_config",
    "resolve_password",
    "resolve_recipients",
    "send_mail",
    "store_password",
]

__version__ = "0.1.0"


def send_mail(
    *,
    subject: str,
    body: str,
    to: str | Iterable[str],
    html: str | None = None,
    cc: str | Iterable[str] = (),
    bcc: str | Iterable[str] = (),
    attachments: Iterable[Path | str] = (),
    account: str | None = None,
    config: Config | None = None,
) -> SendReceipt:
    """Send one message and close the connection.

    Parameters
    ----------
    to, cc, bcc
        Email addresses, address-book aliases, or a mix. A lone string is one
        recipient. `bcc` addresses are delivered without appearing in any header.
        `to` is required: nothing is ever addressed on your behalf.
    subject, body
        `body` is the plain-text part, sent verbatim -- newlines stay newlines.
    html
        Optional HTML alternative. Clients that render it show this; the rest
        fall back to `body`.
    attachments
        Paths to attach. MIME types are guessed from the suffix.
    account
        Which configured mailbox to send as. Defaults to `default_account`.
    config
        Loaded configuration. Read from disk when omitted.

    Returns
    -------
    SendReceipt
        Which recipients the server took, and which it refused. Check
        `receipt.is_complete` -- a partial refusal is reported here, not raised.

    Raises
    ------
    Everything below descends from `MailmailError` except the last entry, which is
    the standard library's own and is passed through untranslated. A caller who
    must catch every way a send can fail writes
    `except (MailmailError, smtplib.SMTPException, OSError)`.

    ConfigError, UnknownAccountError
        The configuration is missing or does not define the account.
    UnknownContactError, ContactCycleError
        A recipient is neither an address nor a resolvable alias.
    InvalidMessageError
        Every recipient resolved to nobody, the subject is blank, or an address
        contains a line break. Also a `ValueError`.
    AttachmentError
        An attachment path does not exist or is not a regular file.
    BlockedAttachmentError, UnscannableArchiveError, EncryptedArchiveError
        The provider would reject the attachment -- a blocked file type, or an
        archive that cannot be scanned to the bottom. Nothing was sent.
    MessageTooLargeError
        The message is over the server's limit; nothing was sent.
    MissingPasswordError, InsecureCredentialsError
        No password is stored for the account, or the credentials file is
        readable by someone other than its owner.
    AuthenticationFailedError
        The server rejected the password; the message says what it wants instead.
    RecipientRefusedError
        The server refused every recipient.
    smtplib.SMTPException, OSError
        The session or the network failed -- the server hung up, DNS did not
        answer, the connection timed out, or the server's certificate is not
        trusted (`ssl.SSLCertVerificationError`, an `OSError`). Not a
        `MailmailError`: these are the standard library's own, and wrapping them
        would say less than they already do.
    """
    config = config if config is not None else load_config()
    smtp_account = config.resolve_account(account)
    book = config.address_book
    message = Message.compose(
        subject     = subject,
        body        = body,
        html        = html,
        to          = resolve_recipients(to, address_book=book),
        cc          = resolve_recipients(cc, address_book=book),
        bcc         = resolve_recipients(bcc, address_book=book),
        attachments = tuple(Attachment.from_path(path) for path in attachments),
    )
    return Mailer(smtp_account).send(message)
