"""The mail being sent: from a loose request, to MIME on the wire.

A `Mail` is the loose request a caller hands in -- addresses or aliases, files by
path, nothing validated yet. `compose_message` resolves it against an address
book into a `Message`, the strict content core, and `Message.to_mime()` turns
that into the bytes the server receives. A `Message` is content only -- it does
not know which account will carry it, so the sender address arrives at
`to_mime()` from the outside.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Self

from mailmail.attachment import Attachment
from mailmail.contacts import AddressBook, resolve_recipients
from mailmail.errors import InvalidMessageError

__all__ = ["Mail", "Message", "compose_message"]


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
        A recipient field is a bare string; no recipient was given at all; the
        subject is blank; the subject or an address contains a line break; or the
        body or HTML carries a character no server can send (an unpaired
        surrogate). Also a `ValueError`.
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
        if "\r" in self.subject or "\n" in self.subject:
            # Same reason the addresses are checked below, plus a sharper one: a
            # newline in a header value lets a caller inject further headers (a
            # Bcc, say). email would raise a bare ValueError at header assignment,
            # outside send()'s documented catch; refuse it here as a MailmailError.
            raise InvalidMessageError(
                f"a line break in the subject is not something any server will "
                f"take: {self.subject!r}"
            )
        for field_name, text in (("body", self.body), ("html", self.html)):
            if text is None:
                continue
            try:
                text.encode("utf-8")
            except UnicodeEncodeError as err:
                # A lone surrogate (from os.fsdecode / a surrogateescape decode) is
                # not encodable to any charset; set_content / add_alternative would
                # raise a bare UnicodeEncodeError at flatten, outside send()'s catch.
                raise InvalidMessageError(
                    f"the {field_name} carries a character no mail server can send "
                    f"(an unpaired surrogate): {err}"
                ) from err
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
        # The stdlib header parser rejects a malformed address (a sender username
        # like "user@", a recipient "user@[bad") by raising an assortment of
        # undocumented types -- IndexError, AttributeError, ValueError -- none of
        # them a MailmailError. Convert any of them here, at the one boundary where
        # an address becomes a header, so a bad address stays inside send()'s catch
        # surface rather than escaping as a bare traceback. The block does nothing
        # else that can raise, so the broad catch masks nothing of ours.
        try:
            mime["From"] = sender
            # Both conditional: a message addressed only to a cc has no To
            # recipients, and an empty `To:` header is not the way to say that --
            # it is a malformed header that some filters read as a spam signal.
            if self.to:
                mime["To"] = ", ".join(self.to)
            if self.cc:
                mime["Cc"] = ", ".join(self.cc)
        except Exception as err:
            raise InvalidMessageError(
                f"an address will not go in a mail header: {err}"
            ) from err
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


@dataclass(frozen=True, slots=True, kw_only=True)
class Mail:
    """One message to send, in the loose vocabulary `send` accepts.

    The per-item input to `send_bulk`, and the shape `send`'s own arguments
    take on their way in: recipients may be addresses or address-book aliases (a
    lone string is one recipient), and attachments are paths. `compose_message`
    resolves those into the addresses and `Attachment`s a `Message` holds -- what
    separates a `Mail` from a `Message` is resolution, not strictness. A `Mail`
    is the request you hand in; a `Message` is what the package composes it into.

    The iterable fields are normalised to tuples on construction, so a `Mail` is
    a stable, re-readable, hashable record like any other frozen dataclass. What
    it does not do is validate: an unknown alias, a blank subject, or a message
    that resolves to no recipient is caught when the `Mail` becomes a `Message`,
    the one place that check lives.

    Attributes
    ----------
    subject, body
        The plain-text parts, sent as written.
    to, cc, bcc
        Addresses, address-book aliases, or a mix. A lone string is one
        recipient. `bcc` is delivered without appearing in any header.
    html
        Optional HTML alternative to `body`.
    attachments
        Paths to attach.
    """

    subject: str
    body: str
    to: str | Iterable[str]
    cc: str | Iterable[str] = ()
    bcc: str | Iterable[str] = ()
    html: str | None = None
    attachments: Iterable[Path | str] = ()

    def __post_init__(self) -> None:
        # A frozen dataclass promises a stable, hashable value; a field left as a
        # bare list or a generator breaks both -- a generator reads empty the
        # second time, and hash() of a list raises. Normalise to tuples the way
        # Message does, keeping a lone recipient string as the one-recipient
        # shorthand rather than a tuple of its characters.
        for field_name in ("to", "cc", "bcc"):
            recipient_field = getattr(self, field_name)
            if not isinstance(recipient_field, str):
                object.__setattr__(self, field_name, tuple(recipient_field))
        object.__setattr__(self, "attachments", tuple(self.attachments))


def compose_message(mail: Mail, *, address_book: AddressBook) -> Message:
    """Resolve a loose `Mail` into the `Message` that will be sent.

    Expands address-book aliases and turns attachment paths into `Attachment`s,
    then hands the resolved parts to `Message.compose`. This is the single place
    the loose request becomes the strict content core, so `send` and
    `send_bulk` compose a message identically.

    Raises
    ------
    UnknownContactError, ContactCycleError
        A recipient is neither an address nor a resolvable alias.
    AttachmentError
        An attachment path is missing, is not a regular file, names an
        unresolvable `~user`, or has a name that is not valid UTF-8.
    InvalidMessageError
        The result has no recipient, a blank subject, a line break in the subject
        or an address, or an unsendable body/HTML.
    """
    return Message.compose(
        subject     = mail.subject,
        body        = mail.body,
        html        = mail.html,
        to          = resolve_recipients(mail.to, address_book=address_book),
        cc          = resolve_recipients(mail.cc, address_book=address_book),
        bcc         = resolve_recipients(mail.bcc, address_book=address_book),
        attachments = tuple(Attachment.from_path(path) for path in mail.attachments),
    )


def _refuse_bare_string(field_name: str, field_value: object) -> None:
    """Reject a lone string where a tuple of addresses belongs.

    A `str` is itself an iterable of characters, so `to="a@b.com"` would be
    sixteen single-character recipients rather than one address. A caller who
    type-checks hears about it first (the hints reach them; see `py.typed`), and
    this is what catches everyone else -- silently sending sixteen messages is
    not a thing to leave to whether someone ran mypy. The constructor is strict
    on purpose; the string shorthand lives on `Message.compose`.
    """
    if isinstance(field_value, str):
        raise InvalidMessageError(
            f"{field_name} takes a tuple of addresses, not a bare string: "
            f"{field_name}={field_value!r} would send one message per character. "
            f"Write {field_name}=({field_value!r},), or use "
            f"Message.compose({field_name}={field_value!r}, ...)"
        )


def _as_address_tuple(addresses: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(addresses, str):
        return (addresses,)
    return tuple(addresses)


def _domain_of(address: str) -> str:
    """The ASCII domain half of an address, for stamping the Message-ID.

    Left to itself `make_msgid` uses the local hostname, which leaks the sending
    machine's name into every message.

    IDNA-encoded because a Message-ID must be ASCII (RFC 5322): an
    internationalized domain (`도메인.한국`) left raw makes the header raise a bare
    `UnicodeEncodeError` at flatten -- outside send()'s catch -- on every send. A
    domain that will not IDNA-encode falls back to the same `localhost` as an
    address with no domain at all.
    """
    _local, _, domain = address.rpartition("@")
    if not domain:
        return "localhost"
    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return "localhost"
