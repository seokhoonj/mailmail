"""The public one-liner: which recipients a message actually goes to.

There are no configured default recipients, and the absence is the thing worth
pinning. Every address on the envelope was named at the call site, so omitting
cc means no cc -- not a cc from a file the caller was not looking at.

It was not always so. A `[defaults]` table used to fill in whatever the call did
not mention, which read as a convenience and behaved as a hazard: name only a
recipient and the configured cc rode along, including on the note meant for one
person. `to` is required now, so nothing is ever addressed on anyone's behalf.
"""

import io
import tarfile

import pytest

from mailmail import (
    Config,
    InvalidMessageError,
    MailmailError,
    SmtpAccount,
    TooManyRecipientsError,
    send,
)
from mailmail.credentials import store_password
from mailmail.provider import NAVER

ACCOUNT = SmtpAccount(name="naver", username="me@example.com", provider=NAVER)


@pytest.fixture(autouse=True)
def _password_on_file(fake_smtp):
    """Every test here sends, and sending needs a password stored."""
    store_password(ACCOUNT, "app-pw")

ADDRESS_BOOK = {
    "lead":     ("lead@example.com",),
    "reviewer": ("reviewer@example.com",),
    "me":       ("me@example.com",),
    "team":     ("lead", "reviewer"),
}


def _make_config(**overrides):
    fields = {
        "default_account": "naver",
        "account_by_name": {"naver": ACCOUNT},
        "address_book":    ADDRESS_BOOK,
    }
    return Config(**(fields | overrides))


def _headers_of(server):
    """The To/Cc the server was actually handed."""
    return server.header("To"), server.header("Cc")


class TestTheRecipientsAreTheOnesNamed:
    def test_a_named_alias_is_resolved(self, fake_smtp):
        send(to="lead", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == ["lead@example.com"]

    def test_a_bare_address_needs_no_address_book(self, fake_smtp):
        send(to="someone@example.com", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == ["someone@example.com"]

    def test_a_group_alias_expands(self, fake_smtp):
        send(to="team", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == [
            "lead@example.com",
            "reviewer@example.com",
        ]

    def test_addresses_and_aliases_mix(self, fake_smtp):
        send(
            to      = ["lead", "outside@example.com"],
            subject = "s",
            body    = "b",
            config  = _make_config(),
        )
        assert fake_smtp.sent_messages[0].recipients == [
            "lead@example.com",
            "outside@example.com",
        ]


class TestNothingRidesAlongUnnamed:
    """The guarantee that replaced the default-recipient machinery."""

    def test_omitting_cc_means_no_cc(self, fake_smtp):
        send(to="me", subject="s", body="b", config=_make_config())
        to_header, cc_header = _headers_of(fake_smtp)
        assert to_header == "me@example.com"
        assert cc_header is None
        assert fake_smtp.sent_messages[0].recipients == ["me@example.com"]

    def test_a_note_to_yourself_reaches_only_yourself(self, fake_smtp):
        """The exact message the old default cc would have copied to someone
        else. It is the reason the feature is gone, so it is a test."""
        send(to="me", subject="reminder", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == ["me@example.com"]

    def test_omitting_bcc_means_no_bcc(self, fake_smtp):
        send(to="me", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == ["me@example.com"]


class TestCcAndBccWhenNamed:
    def test_cc_is_delivered_and_shown(self, fake_smtp):
        send(to="me", cc="lead", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == [
            "me@example.com",
            "lead@example.com",
        ]
        assert _headers_of(fake_smtp)[1] == "lead@example.com"

    def test_bcc_is_delivered_without_a_header(self, fake_smtp):
        send(to="me", bcc="reviewer", subject="s", body="b", config=_make_config())
        assert "reviewer@example.com" in fake_smtp.sent_messages[0].recipients
        assert "reviewer@example.com" not in fake_smtp.last_payload.decode()

    def test_a_cc_only_message_still_delivers(self, fake_smtp):
        send(to=(), cc="lead", subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages[0].recipients == ["lead@example.com"]


class TestAMessageWithNobodyToSendTo:
    def test_emptying_every_recipient_is_refused(self, fake_smtp):
        with pytest.raises(InvalidMessageError, match="at least one recipient"):
            send(to=(), cc=(), subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages == []

    def test_it_is_also_a_value_error(self, fake_smtp):
        # The promise in the errors module: a bad argument is still a ValueError.
        with pytest.raises(ValueError, match="at least one recipient"):
            send(to=(), subject="s", body="b", config=_make_config())

    def test_omitting_to_entirely_is_a_type_error(self, fake_smtp):
        """`to` is required, so the checker catches this before it ever runs.

        Kept as a test anyway because the type hint is the guarantee -- if `to`
        ever regains a default, this fails and says so.
        """
        with pytest.raises(TypeError, match="to"):
            send(subject="s", body="b", config=_make_config())  # type: ignore[call-arg]
        assert fake_smtp.sent_messages == []


class TestTheProviderRecipientCap:
    """Naver and Gmail cap one message at 100 recipients; refuse past it locally."""

    def test_more_than_the_cap_fails_before_connecting(self, fake_smtp):
        recipients = [f"user{i}@example.com" for i in range(101)]  # over the 100 cap
        with pytest.raises(TooManyRecipientsError):
            send(to=recipients, subject="s", body="b", config=_make_config())
        assert fake_smtp.sent_messages == []
        assert fake_smtp.connections == []

    def test_exactly_the_cap_is_allowed(self, fake_smtp):
        recipients = [f"user{i}@example.com" for i in range(100)]
        send(to=recipients, subject="s", body="b", config=_make_config())
        assert len(fake_smtp.sent_messages) == 1


class TestGuardedInputsFailThroughSendAsAMailmailError:
    """The release claim, exercised at the public entry point: a guarded input
    fails as a MailmailError -- what send() documents its caller can catch -- and
    nothing goes out over the wire."""

    def test_a_subject_line_break_fails_the_send(self, fake_smtp):
        with pytest.raises(MailmailError):
            send(
                to      = "me",
                subject = "s\r\nBcc: attacker@example.com",
                body    = "b",
                config  = _make_config(),
            )
        assert fake_smtp.sent_messages == []

    def test_a_surrogate_body_fails_the_send(self, fake_smtp):
        with pytest.raises(MailmailError):
            send(to="me", subject="s", body="hi \udce9", config=_make_config())
        assert fake_smtp.sent_messages == []

    def test_a_corrupt_archive_attachment_fails_the_send(self, fake_smtp, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz") as archive:
            payload = b"A" * 500_000
            info = tarfile.TarInfo("big.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        raw = bytearray(buf.getvalue())
        raw[-30] ^= 0xFF
        path = tmp_path / "archive.tar.xz"
        path.write_bytes(raw)
        with pytest.raises(MailmailError):
            send(
                to          = "me",
                subject     = "s",
                body        = "b",
                attachments = [path],
                config      = _make_config(),
            )
        assert fake_smtp.sent_messages == []
