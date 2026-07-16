"""Send mail from Python through Gmail or Naver SMTP.

    from mailrun import send_mail

    send_mail(
        to          = "lead",
        subject     = "Weekly report",
        body        = "Hi,\\n\\nThis week's report is attached.\\n\\nBest regards,\\n",
        attachments = ["report.xlsx"],
    )

`to` takes addresses, address-book aliases, or both, and falls back to the
configured default when not mentioned. Anything the provider would reject -- a
blocked file type, an archive it cannot scan, a message over the server's size
limit -- raises before a connection is opened, so failures land at the call site
instead of arriving later as a bounce.

Sending as a particular mailbox is `account="gmail"`; reusing one connection for
a batch is `Mailer`.
"""

from collections.abc import Iterable, Sequence
from pathlib import Path

from mailrun.account import SmtpAccount
from mailrun.attachment import Attachment
from mailrun.config import Config, default_config_path, load_config
from mailrun.contacts import AddressBook, resolve_recipients
from mailrun.credentials import (
    PASSWORD_ENV_VAR,
    default_credentials_path,
    delete_password,
    resolve_password,
    store_password,
)
from mailrun.errors import (
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
    MailrunError,
    MessageTooLargeError,
    MissingPasswordError,
    RecipientRefusedError,
    UnknownAccountError,
    UnknownContactError,
    UnknownProviderError,
)
from mailrun.mailer import Mailer, SendReceipt
from mailrun.message import Message
from mailrun.provider import GMAIL, NAVER, MailProvider

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
    "MailrunError",
    "Message",
    "MessageTooLargeError",
    "MissingPasswordError",
    "RecipientRefusedError",
    "SendReceipt",
    "SmtpAccount",
    "UnknownAccountError",
    "UnknownContactError",
    "UnknownProviderError",
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
    to: str | Iterable[str] | None = None,
    html: str | None = None,
    cc: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    attachments: Sequence[Path | str] = (),
    account: str | None = None,
    config: Config | None = None,
) -> SendReceipt:
    """Send one message and close the connection.

    Parameters
    ----------
    to, cc, bcc
        Email addresses, address-book aliases, or a mix. A lone string is one
        recipient. `bcc` addresses are delivered without appearing in any header.

        `None` means "not mentioned" and takes the configured default; `()` means
        "nobody" and overrides it. The distinction matters: with a default cc
        configured, every message that does not mention cc carries it -- so
        `cc=()` is how a note meant for one person stays that way.
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
    Every error below descends from `MailrunError`, so one `except MailrunError`
    guards the whole send.

    ConfigError, UnknownAccountError
        The configuration is missing or does not define the account.
    UnknownContactError, ContactCycleError
        A recipient is neither an address nor a resolvable alias.
    InvalidMessageError
        Every recipient resolved to nobody, the subject is blank, or an address
        contains a line break. Also a `ValueError`.
    AttachmentError
        An attachment path does not exist or is not a regular file.
    BlockedAttachmentError, EncryptedArchiveError, MessageTooLargeError
        The provider would reject the message; nothing was sent.
    MissingPasswordError, InsecureCredentialsError
        No password is stored for the account, or the credentials file is
        readable by someone other than its owner.
    AuthenticationFailedError
        The server rejected the password; the message says what it wants instead.
    RecipientRefusedError
        The server refused every recipient.
    """
    config = config if config is not None else load_config()
    smtp_account = config.resolve_account(account)
    message = Message(
        subject     = subject,
        body        = body,
        html        = html,
        to          = _resolve(to, config.default_to, config),
        cc          = _resolve(cc, config.default_cc, config),
        bcc         = _resolve(bcc, config.default_bcc, config),
        attachments = tuple(Attachment.from_path(path) for path in attachments),
    )
    return Mailer(smtp_account).send(message)


def _resolve(
    named: str | Iterable[str] | None,
    configured_default: tuple[str, ...],
    config: Config,
) -> list[str]:
    """Recipients for one header, falling back to the configured default.

    `None` is the only value that reaches the default: an explicit `()` means the
    caller said "nobody" and is honoured.
    """
    wanted = configured_default if named is None else named
    return resolve_recipients(wanted, address_book=config.address_book)
