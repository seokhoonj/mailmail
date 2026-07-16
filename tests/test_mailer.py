"""Sending, against a fake SMTP server -- nothing leaves the machine.

The real client is replaced at `mailrun.mailer.smtplib`, so these tests exercise
the whole path from `Message` down to the exact `send_message` call the server
would see, deterministically and offline.
"""

import smtplib

import pytest

from mailrun.account import SmtpAccount
from mailrun.attachment import Attachment
from mailrun.credentials import store_password
from mailrun.errors import (
    AuthenticationFailedError,
    BlockedAttachmentError,
    MessageTooLargeError,
    RecipientRefusedError,
)
from mailrun.mailer import Mailer
from mailrun.message import Message
from mailrun.provider import GMAIL, MailProvider

ACCOUNT = SmtpAccount(name="gmail", username="sender@example.com", provider=GMAIL)

# A real provider that happens to accept almost nothing, so the size gate can be
# tripped by an ordinary message instead of by a 35 MB fixture or a patched
# check.
TINY_LIMIT_PROVIDER = MailProvider(
    name               = "gmail",
    smtp_host          = "smtp.gmail.com",
    smtp_port          = 587,
    security           = "starttls",
    blocked_extensions = GMAIL.blocked_extensions,
    max_message_bytes  = 10,
    login_requirements = GMAIL.login_requirements,
)
TINY_LIMIT_ACCOUNT = SmtpAccount(
    name="gmail", username="sender@example.com", provider=TINY_LIMIT_PROVIDER
)


class FakeSmtp:
    """Records what a real server would have been told."""

    def __init__(self, *, refusals=None, advertised_size=35_882_577):
        self.esmtp_features = (
            {"size": str(advertised_size)} if advertised_size is not None else {}
        )
        self.sent = []
        self.logins = []
        self.started_tls = False
        self.quit_count = 0
        self.close_count = 0
        self.rejects_login = False
        self.refusals = refusals or {}

    def ehlo(self):
        return 250, b"ok"

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logins.append((username, password))
        if self.rejects_login:
            raise smtplib.SMTPAuthenticationError(
                534, b"5.7.9 Application-specific password required.\n5.7.9 Learn more"
            )

    def close(self):
        self.close_count += 1

    def send_message(self, mime, from_addr, to_addrs):
        self.sent.append({"mime": mime, "from": from_addr, "to": list(to_addrs)})
        if self.refusals and set(self.refusals) >= set(to_addrs):
            raise smtplib.SMTPRecipientsRefused(self.refusals)
        return dict(self.refusals)

    def quit(self):
        self.quit_count += 1


@pytest.fixture
def fake_smtp(monkeypatch, tmp_path):
    """Install one FakeSmtp behind smtplib.SMTP and hand it back.

    The credentials file is redirected into tmp_path, so the tests neither read
    nor write the real one.
    """
    server = FakeSmtp()
    installed = []

    def fake_smtp_class(host, port, timeout):
        installed.append({"host": host, "port": port, "timeout": timeout})
        return server

    monkeypatch.setattr("mailrun.mailer.smtplib.SMTP", fake_smtp_class)
    monkeypatch.setenv("MAILRUN_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("MAILRUN_PASSWORD", raising=False)
    store_password(ACCOUNT, "app-pw")
    server.connections = installed
    return server


def a_message(**overrides):
    fields = {"subject": "Weekly report", "body": "FYI", "to": "lead@example.com"}
    return Message(**(fields | overrides))


class TestConnection:
    def test_connects_to_the_providers_host_and_port(self, fake_smtp):
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.connections[0]["host"] == "smtp.gmail.com"
        assert fake_smtp.connections[0]["port"] == 587

    def test_starts_tls_before_logging_in(self, fake_smtp):
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.started_tls

    def test_logs_in_with_the_stored_password(self, fake_smtp):
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.logins == [("sender@example.com", "app-pw")]

    def test_password_env_var_beats_the_stored_one(self, fake_smtp, monkeypatch):
        monkeypatch.setenv("MAILRUN_PASSWORD", "from-env")
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.logins == [("sender@example.com", "from-env")]

    def test_bare_send_opens_and_closes_its_own_connection(self, fake_smtp):
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.quit_count == 1

    def test_context_manager_reuses_one_connection_across_sends(self, fake_smtp):
        with Mailer(ACCOUNT) as mailer:
            mailer.send(a_message())
            mailer.send(a_message())
        assert len(fake_smtp.connections) == 1
        assert len(fake_smtp.logins) == 1
        assert fake_smtp.quit_count == 1

    def test_connection_is_closed_even_when_a_send_raises(self, fake_smtp):
        with pytest.raises(ValueError), Mailer(ACCOUNT):
            raise ValueError("something went wrong mid-batch")
        assert fake_smtp.quit_count == 1

    def test_repr_shows_the_account_without_the_password(self, fake_smtp):
        shown = repr(Mailer(ACCOUNT))
        assert "gmail" in shown
        assert "app-pw" not in shown


class TestRejectedLogin:
    """A rejected password says what the provider actually wants.

    "Authentication failed" alone sends the reader hunting for a typo, when the
    real cause is nearly always that Gmail and Naver refuse the account login
    password and want an app password.
    """

    def test_rejected_login_raises_a_domain_error_not_an_smtplib_one(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(a_message())

    def test_error_names_the_account_and_the_provider(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(a_message())
        message = str(caught.value)
        assert "sender@example.com" in message
        assert "gmail" in message

    def test_error_carries_the_providers_setup_requirements(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(a_message())
        message = str(caught.value)
        assert "app password" in message
        assert "myaccount.google.com/apppasswords" in message

    def test_error_quotes_the_servers_own_reply_on_one_line(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(a_message())
        message = str(caught.value)
        assert "534" in message
        assert "Application-specific password required." in message
        assert "\n" not in message  # the reply is multi-line; the message is not

    def test_original_smtplib_error_is_kept_as_the_cause(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(a_message())
        assert isinstance(caught.value.__cause__, smtplib.SMTPAuthenticationError)

    def test_rejected_login_does_not_leave_the_socket_open(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.close_count == 1

    def test_nothing_is_sent_when_the_login_is_rejected(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.sent == []


class TestEnvelope:
    def test_bcc_reaches_the_server_without_appearing_in_the_message(self, fake_smtp):
        Mailer(ACCOUNT).send(
            a_message(to="lead@example.com", bcc="audit@example.com")
        )
        sent = fake_smtp.sent[0]
        assert "audit@example.com" in sent["to"]
        assert "audit@example.com" not in sent["mime"].as_string()

    def test_envelope_carries_to_cc_and_bcc_alike(self, fake_smtp):
        Mailer(ACCOUNT).send(
            a_message(
                to="lead@example.com",
                cc="analyst@example.com",
                bcc="audit@example.com",
            )
        )
        assert fake_smtp.sent[0]["to"] == [
            "lead@example.com",
            "analyst@example.com",
            "audit@example.com",
        ]

    def test_envelope_sender_is_the_account(self, fake_smtp):
        Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.sent[0]["from"] == "sender@example.com"


class TestReceipt:
    def test_clean_send_reports_every_recipient_accepted(self, fake_smtp):
        receipt = Mailer(ACCOUNT).send(a_message(to="lead@example.com"))
        assert receipt.is_complete
        assert receipt.accepted == ("lead@example.com",)
        assert receipt.reason_by_refused_recipient == {}

    def test_receipt_carries_the_message_id(self, fake_smtp):
        receipt = Mailer(ACCOUNT).send(a_message())
        assert receipt.message_id.endswith("@example.com>")

    def test_partial_refusal_is_reported_not_raised(self, fake_smtp):
        fake_smtp.refusals = {"typo@example.com": (550, b"No such user")}
        receipt = Mailer(ACCOUNT).send(
            a_message(to=["lead@example.com", "typo@example.com"])
        )
        assert not receipt.is_complete
        assert receipt.accepted == ("lead@example.com",)
        assert "550" in receipt.reason_by_refused_recipient["typo@example.com"]

    def test_every_recipient_refused_raises(self, fake_smtp):
        fake_smtp.refusals = {"lead@example.com": (550, b"No such user")}
        with pytest.raises(RecipientRefusedError, match="nothing was sent"):
            Mailer(ACCOUNT).send(a_message(to="lead@example.com"))


class TestChecksRunBeforeConnecting:
    """A message the provider would reject never reaches the network."""

    def test_blocked_attachment_is_caught_without_opening_a_connection(
        self, fake_smtp, tmp_path
    ):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        with pytest.raises(BlockedAttachmentError):
            Mailer(ACCOUNT).send(
                a_message(attachments=(Attachment.from_path(installer),))
            )
        assert fake_smtp.connections == []

    def test_oversized_message_is_caught_without_opening_a_connection(self, fake_smtp):
        with pytest.raises(MessageTooLargeError):
            Mailer(TINY_LIMIT_ACCOUNT).send(a_message())
        assert fake_smtp.connections == []


class TestServerAdvertisedLimit:
    """The server's own SIZE beats the provider constant, which can go stale."""

    def test_message_the_server_says_is_too_big_is_refused_before_sending(
        self, fake_smtp
    ):
        fake_smtp.esmtp_features = {"size": "10"}  # server accepts 10 bytes
        with pytest.raises(MessageTooLargeError):
            Mailer(ACCOUNT).send(a_message())
        assert fake_smtp.sent == []

    def test_server_that_advertises_no_size_falls_back_to_the_constant(
        self, fake_smtp
    ):
        fake_smtp.esmtp_features = {}
        receipt = Mailer(ACCOUNT).send(a_message())
        assert receipt.is_complete

    def test_unparseable_advertised_size_falls_back_to_the_constant(self, fake_smtp):
        fake_smtp.esmtp_features = {"size": "unlimited"}
        receipt = Mailer(ACCOUNT).send(a_message())
        assert receipt.is_complete
