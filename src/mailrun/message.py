"""The mail being sent, and the MIME form it goes out as.

A `Message` is content only -- it does not know which account will carry it, so
the sender address arrives at `to_mime()` from the outside.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Self

from mailrun.attachment import Attachment
from mailrun.errors import InvalidMessageError

__all__ = ["Message"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Message:
    """Subject, body, recipients, and attachments.

    The constructor is strict where it counts: a bare string is refused rather
    than accepted, while any other iterable is normalised to the tuple the field
    promises. `Message.compose` is the loose door, where a lone address may be a
    plain string.

    The split is not fussiness. A dataclass field annotation is also its
    `__init__` parameter type -- one site, two roles -- so a field cannot be
    honest about a stored tuple and an accepted string at once. And the refusal
    earns its keep on its own: a `str` is an iterable of characters, so an
    accepted `to="a@b.com"` is sixteen single-character recipients. The hints
    ship (see `py.typed`), so a caller who runs a type checker is told at author
    time -- but most callers do not run one, and the sixteen are silent.

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

    Raises
    ------
    InvalidMessageError
        A recipient field is a bare string, no recipient was given at all, the
        subject is blank, or an address contains a line break. Also a
        `ValueError`.
    """

    subject: str
    body: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()

    @classmethod
    def compose(
        cls,
        *,
        subject: str,
        body: str,
        to: str | Iterable[str],
        cc: str | Iterable[str] = (),
        bcc: str | Iterable[str] = (),
        html: str | None = None,
        attachments: Iterable[Attachment] = (),
    ) -> Self:
        """A message from loose recipients: a lone address may be a plain string.

        This is where the string shorthand lives, and the constructor stays
        strict, because a dataclass field annotation is one site serving two
        roles -- it is also the `__init__` parameter type. Writing
        `to: str | tuple[str, ...]` to be honest about what is accepted makes it
        dishonest about what is stored, since `message.to` is never a string.
        Splitting the two is what the standard library and every record type
        around it do: the attribute says what it holds, and the breadth goes on
        the signature that does the accepting.
        """
        return cls(
            subject     = subject,
            body        = body,
            to          = _as_address_tuple(to),
            cc          = _as_address_tuple(cc),
            bcc         = _as_address_tuple(bcc),
            html        = html,
            attachments = tuple(attachments),
        )

    def __post_init__(self) -> None:
        for name in ("to", "cc", "bcc"):
            _refuse_bare_string(name, getattr(self, name))
        object.__setattr__(self, "to", tuple(self.to))
        object.__setattr__(self, "cc", tuple(self.cc))
        object.__setattr__(self, "bcc", tuple(self.bcc))
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


def _refuse_bare_string(field_name: str, value: object) -> None:
    """Reject a lone string where a tuple of addresses belongs.

    A `str` is itself an iterable of characters, so `to="a@b.com"` would be
    sixteen single-character recipients rather than one address. A caller who
    type-checks hears about it first (the hints reach them; see `py.typed`), and
    this is what catches everyone else -- silently sending sixteen messages is
    not a thing to leave to whether someone ran mypy. The constructor is strict
    on purpose; the string shorthand lives on `Message.compose`.
    """
    if isinstance(value, str):
        raise InvalidMessageError(
            f"{field_name} takes a tuple of addresses, not a bare string: "
            f"{field_name}={value!r} would send one message per character. "
            f"Write {field_name}=({value!r},), or use "
            f"Message.compose({field_name}={value!r}, ...)"
        )


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
