"""The mail being sent, and the MIME form it goes out as.

A `Message` is content only -- it does not know which account will carry it, so
the sender address arrives at `to_mime()` from the outside.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from mailrun.attachment import Attachment
from mailrun.errors import InvalidMessageError

__all__ = ["Message"]


@dataclass(frozen=True, slots=True)
class Message:
    """Subject, body, recipients, and attachments.

    A single address may be given as a plain string anywhere a list is accepted;
    it is normalized to a one-element tuple. This matters more than it looks: the
    fields are tuples of strings, and a bare `str` is itself an iterable of
    characters, so without normalization `to="a@b.com"` would quietly become
    twenty-odd single-character recipients.

    Attributes
    ----------
    subject, body
        The plain-text parts. `body` is sent as-is -- no newline-to-`<br>`
        rewriting; pass `html` when you want markup.
    html
        Optional HTML alternative. When present the message goes out as
        multipart/alternative, and `body` is the fallback for clients that cannot
        render HTML.
    to, cc, bcc
        Recipients. `bcc` is delivered but never written into a header.
    """

    subject: str
    body: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", _as_address_tuple(self.to))
        object.__setattr__(self, "cc", _as_address_tuple(self.cc))
        object.__setattr__(self, "bcc", _as_address_tuple(self.bcc))
        object.__setattr__(self, "attachments", tuple(self.attachments))
        if not self.recipients:
            raise InvalidMessageError("a message needs at least one recipient")
        if not self.subject.strip():
            raise InvalidMessageError("a message needs a subject")
        for address in self.recipients:
            if "\r" in address or "\n" in address:
                # to and cc get this for free when they are written as headers,
                # and bcc -- which never becomes a header -- would otherwise not
                # be caught until smtplib refused it, with the connection open
                # and the login already spent.
                raise InvalidMessageError(
                    f"a line break in an address is not something any server "
                    f"will take: {address!r}"
                )

    @property
    def recipients(self) -> tuple[str, ...]:
        """Every address the message is delivered to, across to/cc/bcc."""
        return self.to + self.cc + self.bcc

    def to_mime(self, *, sender: str) -> EmailMessage:
        """Assemble the MIME message `sender` would put on the wire.

        Non-ASCII subjects and bodies need no special handling: `EmailMessage`
        applies RFC 2047 header encoding and picks a UTF-8 transfer encoding for
        the body on its own.

        Parameters
        ----------
        sender
            The `From` address -- normally the sending account's username.
        """
        mime = EmailMessage()
        mime["From"] = sender
        # Both conditional: a message addressed only to a cc has no To
        # recipients, and an empty `To:` header is not the way to say that --
        # it is a malformed header that some filters read as a spam signal.
        if self.to:
            mime["To"] = ", ".join(self.to)
        if self.cc:
            mime["Cc"] = ", ".join(self.cc)
        # Bcc is deliberately absent: writing the header would show every blind
        # recipient to all the others. The addresses reach the server through the
        # SMTP envelope instead (see Mailer.send).
        mime["Subject"] = self.subject
        mime["Date"] = formatdate(localtime=True)
        mime["Message-ID"] = make_msgid(domain=_domain_of(sender))
        mime.set_content(self.body)
        if self.html is not None:
            mime.add_alternative(self.html, subtype="html")
        for attachment in self.attachments:
            maintype, _, subtype = attachment.mime_type.partition("/")
            mime.add_attachment(
                attachment.path.read_bytes(),
                maintype = maintype,
                subtype  = subtype,
                filename = attachment.filename,
            )
        return mime


def _as_address_tuple(value: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _domain_of(address: str) -> str:
    """The domain half of an address, for stamping the Message-ID.

    Left to itself `make_msgid` uses the local hostname, which leaks the sending
    machine's name into every message.
    """
    _local, _, domain = address.rpartition("@")
    return domain or "localhost"
