"""Sending many messages over one connection, against the fake server.

send_bulk is a mail merge: one Mail per recipient, all sent as one account over
a single login. The two guarantees a bare Mailer loop lacks are what these pin --
every mail is screened before the connection opens, and one refused recipient
does not sink the rest of the batch.
"""

import smtplib

import pytest

from mailmail import (
    BlockedAttachmentError,
    Config,
    InvalidMessageError,
    Mail,
    MessageTooLargeError,
    SmtpAccount,
    UnknownContactError,
    send_bulk,
)
from mailmail.credentials import store_password
from mailmail.provider import GMAIL, NAVER, MailProvider

ACCOUNT = SmtpAccount(name="naver", username="me@example.com", provider=NAVER)

ADDRESS_BOOK = {
    "lead":     ("lead@example.com",),
    "reviewer": ("reviewer@example.com",),
    "team":     ("lead", "reviewer"),
}


@pytest.fixture(autouse=True)
def _password_on_file(fake_smtp):
    """Every test here sends, and sending needs a password stored."""
    store_password(ACCOUNT, "app-pw")


def make_config(**overrides):
    fields = {
        "default_account": "naver",
        "account_by_name": {"naver": ACCOUNT},
        "address_book":    ADDRESS_BOOK,
    }
    return Config(**(fields | overrides))


def make_mail(**overrides):
    fields = {"subject": "s", "body": "b", "to": "someone@example.com"}
    return Mail(**(fields | overrides))


# A real provider whose only oddity is a tiny size limit, so a message can pass
# the attachment-only screen (no attachments -> estimate 0) and still be refused
# by the exact size check once its body is assembled -- the mid-batch edge.
SMALL_LIMIT_PROVIDER = MailProvider(
    name               = "naver",
    smtp_host          = "smtp.naver.com",
    smtp_port          = 587,
    security           = "starttls",
    blocked_extensions = NAVER.blocked_extensions,
    max_message_bytes  = 2000,
    login_requirements = NAVER.login_requirements,
)
SMALL_LIMIT_ACCOUNT = SmtpAccount(
    name="naver", username="me@example.com", provider=SMALL_LIMIT_PROVIDER
)


class TestOneConnectionForTheWholeBatch:
    def test_many_mails_share_one_login(self, fake_smtp):
        send_bulk(
            [
                make_mail(to="a@example.com"),
                make_mail(to="b@example.com"),
                make_mail(to="c@example.com"),
            ],
            config = make_config(),
        )
        assert len(fake_smtp.connections) == 1
        assert len(fake_smtp.logins) == 1
        assert len(fake_smtp.sent_messages) == 3

    def test_an_empty_batch_opens_no_connection(self, fake_smtp):
        receipts = send_bulk([], config=make_config())
        assert receipts == []
        assert fake_smtp.connections == []


class TestAReceiptPerMailInTheOrderGiven:
    def test_one_receipt_per_mail_in_order(self, fake_smtp):
        receipts = send_bulk(
            [make_mail(to="a@example.com"), make_mail(to="b@example.com")],
            config = make_config(),
        )
        assert [receipt.accepted for receipt in receipts] == [
            ("a@example.com",),
            ("b@example.com",),
        ]

    def test_each_mails_aliases_resolve_against_the_address_book(self, fake_smtp):
        receipts = send_bulk(
            [make_mail(to="lead"), make_mail(to="team")],
            config = make_config(),
        )
        assert receipts[0].accepted == ("lead@example.com",)
        assert receipts[1].accepted == ("lead@example.com", "reviewer@example.com")

    def test_each_mail_carries_its_own_body(self, fake_smtp):
        send_bulk(
            [
                make_mail(to="a@example.com", body="Hello A"),
                make_mail(to="b@example.com", body="Hello B"),
            ],
            config = make_config(),
        )
        assert "Hello A" in fake_smtp.sent_messages[0].payload.decode()
        assert "Hello B" in fake_smtp.sent_messages[1].payload.decode()


class TestOneRefusedRecipientDoesNotSinkTheBatch:
    def test_a_fully_refused_mail_is_a_receipt_not_a_raise(self, fake_smtp):
        fake_smtp.refusals = {"b@example.com": (550, b"No such user")}
        receipts = send_bulk(
            [
                make_mail(to="a@example.com"),
                make_mail(to="b@example.com"),
                make_mail(to="c@example.com"),
            ],
            config = make_config(),
        )
        assert receipts[0].is_complete
        assert receipts[2].is_complete
        assert not receipts[1].is_complete
        assert receipts[1].accepted == ()
        assert "550" in receipts[1].reason_by_refused_recipient["b@example.com"]

    def test_a_refusal_does_not_reconnect_for_the_rest(self, fake_smtp):
        fake_smtp.refusals = {"b@example.com": (550, b"No such user")}
        receipts = send_bulk(
            [
                make_mail(to="a@example.com"),
                make_mail(to="b@example.com"),
                make_mail(to="c@example.com"),
            ],
            config = make_config(),
        )
        assert len(receipts) == 3
        assert len(fake_smtp.connections) == 1

    def test_a_partial_refusal_is_reported_on_the_row_it_hit(self, fake_smtp):
        fake_smtp.refusals = {"reviewer@example.com": (550, b"No such user")}
        receipts = send_bulk(
            [make_mail(to="lead"), make_mail(to="team")],
            config = make_config(),
        )
        assert receipts[0].is_complete
        assert not receipts[1].is_complete
        assert receipts[1].accepted == ("lead@example.com",)


class TestABadRowStopsTheBatchBeforeAnythingIsSent:
    """Composition and screening run over every row before the connection opens."""

    def test_a_blocked_attachment_in_one_row_sends_nothing(self, fake_smtp, tmp_path):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        mails = [
            make_mail(to="a@example.com"),
            make_mail(to="b@example.com", attachments=[installer]),
            make_mail(to="c@example.com"),
        ]
        with pytest.raises(BlockedAttachmentError):
            send_bulk(mails, config=make_config())
        assert fake_smtp.connections == []
        assert fake_smtp.sent_messages == []

    def test_an_unknown_alias_in_one_row_sends_nothing(self, fake_smtp):
        mails = [make_mail(to="lead"), make_mail(to="nobody")]
        with pytest.raises(UnknownContactError):
            send_bulk(mails, config=make_config())
        assert fake_smtp.connections == []

    def test_a_blank_subject_in_one_row_sends_nothing(self, fake_smtp):
        mails = [
            make_mail(to="a@example.com"),
            make_mail(to="b@example.com", subject="  "),
        ]
        with pytest.raises(InvalidMessageError):
            send_bulk(mails, config=make_config())
        assert fake_smtp.connections == []


class TestFailuresThatOnlyAppearMidBatch:
    """The honest edge: a row can fail only after earlier rows are on the wire.

    The up-front screen weighs attachments alone, so a row that only crosses the
    size limit once its body is assembled, and a transport drop, both surface
    inside the batch -- after predecessors have gone and with the collected
    receipts lost.
    """

    def test_a_row_over_size_only_once_assembled_fails_after_prior_rows_sent(
        self, fake_smtp
    ):
        config = make_config(account_by_name={"naver": SMALL_LIMIT_ACCOUNT})
        mails = [
            make_mail(to="a@example.com", body="small"),
            make_mail(to="b@example.com", body="x" * 5000),
        ]
        with pytest.raises(MessageTooLargeError):
            send_bulk(mails, config=config)
        assert len(fake_smtp.connections) == 1
        assert [sent.recipients for sent in fake_smtp.sent_messages] == [
            ["a@example.com"]
        ]

    def test_a_transport_failure_mid_batch_propagates_and_stops(self, fake_smtp):
        fake_smtp.sendmail_raises = smtplib.SMTPServerDisconnected("connection dropped")
        fake_smtp.sendmail_raises_on = "b@example.com"
        mails = [
            make_mail(to="a@example.com"),
            make_mail(to="b@example.com"),
            make_mail(to="c@example.com"),
        ]
        with pytest.raises(smtplib.SMTPServerDisconnected):
            send_bulk(mails, config=make_config())
        assert len(fake_smtp.connections) == 1  # one login, no reconnect
        assert [sent.recipients for sent in fake_smtp.sent_messages] == [
            ["a@example.com"]
        ]


class TestTheBatchSendsAsTheChosenAccount:
    def test_no_account_uses_the_default(self, fake_smtp):
        send_bulk([make_mail(to="a@example.com")], config=make_config())
        assert fake_smtp.logins == [("me@example.com", "app-pw")]

    def test_a_named_account_sends_the_whole_batch(self, fake_smtp):
        gmail_account = SmtpAccount(
            name="gmail", username="me@gmail.com", provider=GMAIL
        )
        store_password(gmail_account, "gmail-pw")
        config = make_config(account_by_name={"naver": ACCOUNT, "gmail": gmail_account})
        send_bulk([make_mail(to="a@example.com")], account="gmail", config=config)
        assert fake_smtp.connections[0]["host"] == "smtp.gmail.com"
        assert fake_smtp.logins == [("me@gmail.com", "gmail-pw")]


class TestCcAndBccPerRow:
    def test_a_row_delivers_cc_and_bcc_without_a_bcc_header(self, fake_smtp):
        send_bulk(
            [make_mail(to="lead", cc="reviewer", bcc="audit@example.com")],
            config = make_config(),
        )
        sent = fake_smtp.sent_messages[0]
        assert sent.recipients == [
            "lead@example.com",
            "reviewer@example.com",
            "audit@example.com",
        ]
        assert "audit@example.com" not in sent.payload.decode()
