"""Send mail from Python through Gmail or Naver SMTP.

    from mailmail import send

    send(
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

from collections.abc import Iterable, Sequence
from pathlib import Path

from mailmail.account import SmtpAccount
from mailmail.attachment import Attachment
from mailmail.config import (
    STARTER_CONFIG,
    Config,
    config_dir,
    default_config_path,
    load_config,
)
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
from mailmail.message import Mail, Message, compose_message
from mailmail.provider import GMAIL, NAVER, MailProvider, SmtpSecurity

__all__ = [
    "GMAIL",
    "NAVER",
    "PASSWORD_ENV_VAR",
    "STARTER_CONFIG",
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
    "Mail",
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
    "compose_message",
    "config_dir",
    "default_config_path",
    "default_credentials_path",
    "delete_password",
    "load_config",
    "resolve_password",
    "resolve_recipients",
    "send",
    "send_bulk",
    "store_password",
]

__version__ = "0.2.0"


def send(
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

    ConfigError, UnknownAccountError, UnknownProviderError
        The configuration is missing, names a provider mailmail does not know, or
        does not define the account.
    UnknownContactError, ContactCycleError
        A recipient is neither an address nor a resolvable alias.
    InvalidMessageError
        Every recipient resolved to nobody; the subject is blank; the subject or
        an address contains a line break; or the body or HTML carries a character
        no server can send (an unpaired surrogate). Also a `ValueError`.
    AttachmentError
        An attachment path does not exist, is not a regular file, names an
        unresolvable `~user`, or has a name that is not valid UTF-8.
    BlockedAttachmentError, UnscannableArchiveError, EncryptedArchiveError
        The provider would reject the attachment -- a blocked file type, or an
        archive that cannot be scanned to the bottom. Nothing was sent.
    MessageTooLargeError
        The message is over the server's limit; nothing was sent.
    MissingPasswordError, InsecureCredentialsError, CredentialsError
        No password is stored for the account, the credentials file is readable
        by someone other than its owner, or it is not readable JSON.
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
    mail = Mail(
        subject     = subject,
        body        = body,
        html        = html,
        to          = to,
        cc          = cc,
        bcc         = bcc,
        attachments = attachments,
    )
    message = compose_message(mail, address_book=config.address_book)
    return Mailer(smtp_account).send(message)


def send_bulk(
    mails: Sequence[Mail],
    *,
    account: str | None = None,
    config: Config | None = None,
) -> list[SendReceipt]:
    """Send many messages over one connection, and report each one's result.

    A mail merge in one call: one `Mail` per recipient, personalised however the
    caller built it, all sent as the same account over a single login. The two
    things a plain `with Mailer(...)` loop does not give you are here -- every
    mail is checked before the connection opens, and one refused recipient does
    not sink the rest of the batch.

    Parameters
    ----------
    mails
        One `Mail` per message, held as a sequence so the returned receipts can
        be paired back with it by position. Recipients may be addresses or
        aliases and attachments are paths, exactly as `send` takes them.
    account
        Which configured mailbox to send as. Defaults to `default_account`.
    config
        Loaded configuration. Read from disk when omitted.

    Returns
    -------
    list[SendReceipt]
        One receipt per mail, in the order given, so `zip(mails, receipts)` pairs
        each with its outcome. A message the server refused for every recipient
        is a receipt with empty `accepted`, not a missing entry -- check
        `receipt.is_complete` per row.

    Raises
    ------
    These are raised before the connection opens, so nothing is sent -- one bad
    row stops the whole batch:

    ConfigError, UnknownAccountError, UnknownProviderError
        The configuration is missing, names a provider mailmail does not know, or
        does not define the account.
    UnknownContactError, ContactCycleError, InvalidMessageError
        A row's recipient is not resolvable, or a row has no recipient, a blank
        subject, a line break in the subject, or an unsendable body/HTML.
    AttachmentError
        A row's attachment path does not exist, is not a regular file, names an
        unresolvable `~user`, or has a name that is not valid UTF-8.
    BlockedAttachmentError, UnscannableArchiveError, EncryptedArchiveError
        A row's attachment would be rejected by the provider.
    MessageTooLargeError
        A row's attachments already exceed the size limit.

    Then, when the connection opens -- before the first message goes out, so
    still nothing is sent:

    MissingPasswordError, InsecureCredentialsError, CredentialsError
        No password is stored for the account, the credentials file is readable
        by someone other than its owner, or it is not readable JSON. `ConfigError`
        here too, when the default credentials location has no home directory.
    AuthenticationFailedError
        The server rejected the password.

    These can land mid-batch, after earlier messages have gone out -- the
    exception propagates, the receipts collected so far are lost, and what was
    already sent cannot be unsent:

    MessageTooLargeError
        A row crosses the size limit only once fully assembled; the up-front
        screen weighs attachments alone, not the finished MIME.
    smtplib.SMTPException, OSError
        The session or the network dropped partway through the batch.

    What the server merely refuses per recipient stays in the receipts and never
    raises.
    """
    config = config if config is not None else load_config()
    smtp_account = config.resolve_account(account)
    address_book = config.address_book
    # Compose every message first, so an unresolvable alias or blank subject in
    # any row fails here, before send_many opens a connection.
    messages = [compose_message(mail, address_book=address_book) for mail in mails]
    return Mailer(smtp_account).send_many(messages)
