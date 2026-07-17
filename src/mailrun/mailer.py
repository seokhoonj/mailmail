"""Opening an authenticated SMTP session and putting a message on the wire.

`Mailer` is the one stateful object in the package: it owns a live connection.
Used as a context manager it holds that connection open across sends, which is
what makes a batch cheap; used bare, each `send` opens and closes its own.
"""

import io
import smtplib
import ssl
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from email.generator import BytesGenerator
from email.message import EmailMessage
from types import MappingProxyType, TracebackType
from typing import Self

from mailrun.account import SmtpAccount
from mailrun.attachment import (
    check_attachments,
    check_message_size,
    estimated_encoded_bytes,
)
from mailrun.credentials import resolve_password
from mailrun.errors import AuthenticationFailedError, RecipientRefusedError
from mailrun.message import Message

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "Mailer", "SendReceipt"]

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class SendReceipt:
    """What the server did with a message.

    A send can succeed for some recipients and fail for others, so this reports
    both rather than collapsing the two into a bare success.

    Attributes
    ----------
    message_id
        The `Message-ID` header, for finding the mail again later.
    accepted
        Addresses the server took responsibility for.
    reason_by_refused_recipient
        Addresses the server rejected, each with the reason it gave. Empty on a
        clean send. Read-only: the receipt is a record of what happened, and
        `is_complete` is derived from it, so a caller who could write into the
        mapping could change what the send appears to have done.
    """

    message_id: str
    accepted: tuple[str, ...]
    reason_by_refused_recipient: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_by_refused_recipient",
            MappingProxyType(dict(self.reason_by_refused_recipient)),
        )

    @property
    def is_complete(self) -> bool:
        """Whether every recipient was accepted."""
        return not self.reason_by_refused_recipient


class Mailer:
    """Sends messages as one account.

    Construct it with an account and apply it to messages:

        with Mailer(account) as mailer:
            mailer.send(message)

    Outside a `with` block `send` still works; it just pays for a fresh
    connection each time.

    Parameters
    ----------
    account
        The mailbox to send as.
    timeout_seconds
        How long to wait on the SMTP socket before giving up.

    Raises
    ------
    Constructing one raises nothing -- it opens no connection. Entering the
    `with` block does: see `send` for what a session can raise, all of which
    `__enter__` can raise too.
    """

    def __init__(
        self, account: SmtpAccount, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._account = account
        self._timeout_seconds = timeout_seconds
        self._smtp: smtplib.SMTP | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(account={self._account.name!r}, "
            f"username={self._account.username!r}, "
            f"connected={self._smtp is not None})"
        )

    def __enter__(self) -> Self:
        self._smtp = self._connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        smtp, self._smtp = self._smtp, None
        if smtp is not None:
            _hang_up(smtp)

    def send(self, message: Message) -> SendReceipt:
        """Check, assemble, and send one message.

        Everything the provider would reject is caught here, before the
        connection is opened, so a bad attachment surfaces as an exception at the
        call site rather than as a bounce ten minutes later.

        Raises
        ------
        BlockedAttachmentError, EncryptedArchiveError, MessageTooLargeError
            The provider would refuse the message.
        MissingPasswordError, InsecureCredentialsError, CredentialsError
            No password is stored, the credentials file is readable by others, or
            it is not readable JSON.
        AuthenticationFailedError
            The server rejected the password. Note this is a `MailrunError`, not
            an `smtplib.SMTPException` -- authentication is the one session
            failure this package translates, because "authentication failed" on
            its own never tells the reader that an app password is what is wanted.
        RecipientRefusedError
            The server refused every recipient.
        smtplib.SMTPException
            The session itself failed (connection, protocol).
        """
        provider = self._account.provider
        check_attachments(message.attachments, provider=provider)
        check_message_size(
            estimated_encoded_bytes(message.attachments),
            limit_bytes = provider.max_message_bytes,
        )
        mime = message.to_mime(sender=self._account.username)
        payload = _as_wire_bytes(mime)
        check_message_size(len(payload), limit_bytes=provider.max_message_bytes)
        with self._session() as smtp:
            advertised_limit = _advertised_size_limit(smtp)
            if advertised_limit is not None:
                check_message_size(len(payload), limit_bytes=advertised_limit)
            reason_by_refused_recipient = _send_over(
                smtp, payload, sender=str(mime["From"]), recipients=message.recipients
            )
        return _as_receipt(mime, message.recipients, reason_by_refused_recipient)

    @contextmanager
    def _session(self) -> Iterator[smtplib.SMTP]:
        """The open connection, or a throwaway one when used outside a `with`."""
        if self._smtp is not None:
            yield self._smtp
            return
        smtp = self._connect()
        try:
            yield smtp
        finally:
            _hang_up(smtp)

    def _connect(self) -> smtplib.SMTP:
        """Open an authenticated session, or leave nothing behind trying.

        Every exit between the socket opening and the login succeeding closes it.
        The connection exists from the constructor onward, so any raise after
        that point -- a server that dropped STARTTLS, an auth method neither side
        offers, a disconnect mid-login -- would otherwise drop a connected socket
        with nobody holding a reference to close it.
        """
        provider = self._account.provider
        password = resolve_password(self._account)
        smtp = self._open_socket()
        try:
            if provider.security != "ssl":
                smtp.ehlo()
                smtp.starttls(context=_verifying_tls_context())
            smtp.ehlo()
            smtp.login(self._account.username, password)
        except smtplib.SMTPAuthenticationError as err:
            smtp.close()
            raise AuthenticationFailedError(
                f"{provider.name} rejected the password for "
                f"{self._account.username} ({err.smtp_code} "
                f"{_as_text(err.smtp_error)}). {provider.login_requirements}"
            ) from err
        except BaseException:
            smtp.close()
            raise
        return smtp

    def _open_socket(self) -> smtplib.SMTP:
        provider = self._account.provider
        if provider.security == "ssl":
            return smtplib.SMTP_SSL(
                provider.smtp_host,
                provider.smtp_port,
                timeout = self._timeout_seconds,
                context = _verifying_tls_context(),
            )
        return smtplib.SMTP(
            provider.smtp_host, provider.smtp_port, timeout=self._timeout_seconds
        )


def _verifying_tls_context() -> ssl.SSLContext:
    """A TLS context that checks the server is who it says it is.

    Passed explicitly because smtplib's default is not this. Given no context,
    `starttls` and `SMTP_SSL` build one with `ssl._create_stdlib_context()`,
    which *is* `ssl._create_unverified_context`: `check_hostname` off,
    `verify_mode` CERT_NONE. The handshake then succeeds against any
    certificate, signed by anyone, for any name -- and `login()` sends the app
    password through it.

    That is not a theoretical hole. Anyone on the path -- hostile Wi-Fi, a
    spoofed DNS answer, a transparent proxy -- answers for smtp.gmail.com with a
    certificate they minted themselves and collects the password in the clear.
    Every other precaution here guards the file on disk; this one is the only
    thing guarding the wire, and it was off.
    """
    return ssl.create_default_context()


def _hang_up(smtp: smtplib.SMTP) -> None:
    """End the session, without letting teardown outrank the caller's error.

    `quit()` sends QUIT before it closes, so on a connection the server already
    dropped it raises -- never reaching `close()`, leaking the socket, and, when
    it runs from `__exit__`, replacing whatever the `with` body was raising. The
    dropped connection is exactly when the caller most needs their own error.
    """
    try:
        smtp.quit()
    except smtplib.SMTPException:
        smtp.close()


def _advertised_size_limit(smtp: smtplib.SMTP) -> int | None:
    """The largest message this server says it accepts, from its EHLO reply.

    The server's own number beats the provider constant, which can only be as
    fresh as the last time someone looked. `None` means the server named no
    *usable* limit, by any of three routes: it did not advertise SIZE; it
    advertised zero, which RFC 1870 Sec.3 defines as "no fixed maximum message
    size is in force" and which read literally would refuse every message a
    limitless server was happy to take; or it advertised something that is not a
    number. The enumeration used to close after the first two and the third fell
    through it, which costs a reader debugging a surprising `None` their trust in
    the list. The provider constant still gates the size in all three cases.
    """
    advertised = smtp.esmtp_features.get("size")
    if advertised is None:
        return None
    try:
        limit_bytes = int(advertised)
    except ValueError:
        return None
    return limit_bytes or None


def _as_wire_bytes(mime: EmailMessage) -> bytes:
    """The message exactly as the server will receive it.

    SMTP ends every line with CRLF, and `EmailMessage.as_bytes()` does not -- it
    uses the platform's separator, which is one byte shorter per line. On a 20 MB
    attachment that is a 1.3% understatement, which matters because this is what
    the size gate weighs: measuring the shorter form would pass a message the
    server then refuses for being too large, which is the one outcome the gate
    exists to prevent.

    Flattening once here rather than measuring with `as_bytes()` and letting
    `send_message` flatten again also halves the work: a 25 MB attachment costs
    about a second and 100 MB of peak memory per pass.
    """
    with io.BytesIO() as buffer:
        generator = BytesGenerator(buffer, policy=mime.policy.clone(linesep="\r\n"))
        generator.flatten(mime, linesep="\r\n")
        return buffer.getvalue()


def _send_over(
    smtp: smtplib.SMTP,
    payload: bytes,
    *,
    sender: str,
    recipients: tuple[str, ...],
) -> dict[str, str]:
    """Hand the message to the server; report per-recipient refusals.

    Takes the already-flattened bytes, and the recipients explicitly rather than
    off the headers -- which is what delivers bcc addresses without naming them
    in the message.
    """
    try:
        refused = smtp.sendmail(sender, list(recipients), payload)
    except smtplib.SMTPRecipientsRefused as err:
        refused_all = ", ".join(sorted(err.recipients))
        raise RecipientRefusedError(
            f"the server refused every recipient ({refused_all}); nothing was sent"
        ) from err
    return {address: _as_reason(reply) for address, reply in refused.items()}


def _as_reason(reply: tuple[int, bytes]) -> str:
    code, text = reply
    return f"{code} {_as_text(text)}"


def _as_text(server_reply: bytes | str) -> str:
    """The server's own words, collapsed to one line.

    Replies arrive as bytes, and a rejection often spans several lines; both
    would otherwise land mid-sentence in an error message.
    """
    if isinstance(server_reply, bytes):
        server_reply = server_reply.decode("utf-8", errors="replace")
    return " ".join(server_reply.split())


def _as_receipt(
    mime: EmailMessage,
    recipients: tuple[str, ...],
    reason_by_refused_recipient: dict[str, str],
) -> SendReceipt:
    accepted = tuple(
        address
        for address in recipients
        if address not in reason_by_refused_recipient
    )
    return SendReceipt(
        message_id                  = str(mime["Message-ID"]),
        accepted                    = accepted,
        reason_by_refused_recipient = reason_by_refused_recipient,
    )
