"""Sending, against a fake SMTP server -- nothing leaves the machine.

The real client is replaced at `mailrun.mailer.smtplib` (see conftest), so these
tests exercise the whole path from `Message` down to the exact bytes the server
would be handed, deterministically and offline.
"""

import smtplib
import ssl
from pathlib import Path

import pytest

from mailrun.account import SmtpAccount
from mailrun.attachment import Attachment, estimated_encoded_bytes
from mailrun.credentials import store_password
from mailrun.errors import (
    AuthenticationFailedError,
    BlockedAttachmentError,
    MessageTooLargeError,
    RecipientRefusedError,
)
from mailrun.mailer import Mailer, _as_wire_bytes
from mailrun.message import Message
from mailrun.provider import GMAIL, MailProvider

ACCOUNT = SmtpAccount(name="gmail", username="sender@example.com", provider=GMAIL)


@pytest.fixture(autouse=True)
def _password_on_file(fake_smtp):
    """Every test here sends, and sending needs a password stored."""
    store_password(ACCOUNT, "app-pw")


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


def make_message(**overrides):
    fields = {"subject": "Weekly report", "body": "FYI", "to": "lead@example.com"}
    return Message.compose(**(fields | overrides))


class TestConnection:
    def test_connects_to_the_providers_host_and_port(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.connections[0]["host"] == "smtp.gmail.com"
        assert fake_smtp.connections[0]["port"] == 587

    def test_starts_tls_before_logging_in(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.started_tls

    def test_the_tls_context_checks_who_answered(self, fake_smtp):
        """Starting TLS is not the point; starting it with someone you checked is.

        smtplib's default context verifies nothing -- `ssl._create_stdlib_context`
        is `ssl._create_unverified_context` -- so `started_tls` was true while any
        self-signed certificate was being accepted and the password sent through
        it. tests/test_tls.py proves the refusal against a real impostor; this
        pins the two settings that cause it, where a failure names them.
        """
        Mailer(ACCOUNT).send(make_message())
        context = fake_smtp.starttls_context
        assert context is not None, "no context passed: smtplib would verify nothing"
        assert context.check_hostname is True
        assert context.verify_mode is ssl.CERT_REQUIRED

    def test_logs_in_with_the_stored_password(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.logins == [("sender@example.com", "app-pw")]

    def test_password_env_var_beats_the_stored_one(self, fake_smtp, monkeypatch):
        monkeypatch.setenv("MAILRUN_PASSWORD", "from-env")
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.logins == [("sender@example.com", "from-env")]

    def test_bare_send_opens_and_closes_its_own_connection(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.quit_count == 1

    def test_context_manager_reuses_one_connection_across_sends(self, fake_smtp):
        with Mailer(ACCOUNT) as mailer:
            mailer.send(make_message())
            mailer.send(make_message())
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
            Mailer(ACCOUNT).send(make_message())

    def test_error_names_the_account_and_the_provider(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(make_message())
        message = str(caught.value)
        assert "sender@example.com" in message
        assert "gmail" in message

    def test_error_carries_the_providers_setup_requirements(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(make_message())
        message = str(caught.value)
        assert "app password" in message
        assert "myaccount.google.com/apppasswords" in message

    def test_error_quotes_the_servers_own_reply_on_one_line(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(make_message())
        message = str(caught.value)
        assert "534" in message
        assert "Application-specific password required." in message
        assert "\n" not in message  # the reply is multi-line; the message is not

    def test_original_smtplib_error_is_kept_as_the_cause(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError) as caught:
            Mailer(ACCOUNT).send(make_message())
        assert isinstance(caught.value.__cause__, smtplib.SMTPAuthenticationError)

    def test_rejected_login_does_not_leave_the_socket_open(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.close_count == 1

    def test_nothing_is_sent_when_the_login_is_rejected(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.sent_messages == []


class TestTheSocketIsAlwaysClosed:
    """Every way out of a handshake closes what it opened.

    Only the authentication path used to clean up, so a server that dropped
    STARTTLS, or an auth method neither side offered, left a connected socket
    with nobody holding it.
    """

    def test_starttls_failure_closes_the_socket(self, fake_smtp):
        fake_smtp.starttls_raises = smtplib.SMTPNotSupportedError("no STARTTLS here")
        with pytest.raises(smtplib.SMTPNotSupportedError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.close_count == 1

    def test_non_auth_login_failure_closes_the_socket(self, fake_smtp):
        fake_smtp.login_raises = smtplib.SMTPNotSupportedError("no auth method")
        with pytest.raises(smtplib.SMTPNotSupportedError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.close_count == 1

    def test_rejected_login_still_closes_the_socket(self, fake_smtp):
        fake_smtp.rejects_login = True
        with pytest.raises(AuthenticationFailedError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.close_count == 1


class TestHangingUpDoesNotOutrankTheCallersError:
    """A dead connection must not eat the exception the caller needs to see.

    `quit()` sends QUIT before it closes, so on a connection the server already
    dropped it raises -- from inside `__exit__`, where it replaces whatever the
    `with` body was raising, and without ever reaching `close()`.
    """

    def test_the_bodys_error_survives_a_failing_quit(self, fake_smtp):
        fake_smtp.quit_raises = smtplib.SMTPServerDisconnected("connection is gone")
        with pytest.raises(ValueError, match="the error the caller needs"), Mailer(
            ACCOUNT
        ):
            raise ValueError("the error the caller needs to see")

    def test_a_failing_quit_still_closes_the_socket(self, fake_smtp):
        fake_smtp.quit_raises = smtplib.SMTPServerDisconnected("connection is gone")
        with Mailer(ACCOUNT) as mailer:
            mailer.send(make_message())
        assert fake_smtp.close_count == 1

    def test_a_failing_quit_does_not_surface_from_a_clean_bare_send(self, fake_smtp):
        fake_smtp.quit_raises = smtplib.SMTPServerDisconnected("connection is gone")
        receipt = Mailer(ACCOUNT).send(make_message())
        assert receipt.is_complete


class TestServerThatNamesNoSizeLimit:
    """RFC 1870 Sec.3: a SIZE of zero means no fixed maximum is in force."""

    def test_size_zero_is_read_as_no_limit_not_as_zero_bytes(self, fake_smtp):
        fake_smtp.esmtp_features = {"size": "0"}
        receipt = Mailer(ACCOUNT).send(make_message())
        assert receipt.is_complete


class TestReceiptIsAReadOnlyRecord:
    def test_the_refusal_map_cannot_be_written_into(self, fake_smtp):
        # is_complete is derived from this mapping, so a writable one lets a
        # caller change what the send appears to have done.
        #
        # The type: ignore is the point, not an escape: the field is a Mapping, so
        # mypy refuses the assignment outright and this test proves the runtime
        # refuses it too. Both layers hold, and the ignore is what lets the second
        # be tested at all.
        receipt = Mailer(ACCOUNT).send(make_message())
        with pytest.raises(TypeError):
            receipt.reason_by_refused_recipient["typo@example.com"] = "550 nope"  # type: ignore[index]
        assert receipt.is_complete


class TestWhatIsWeighedIsWhatIsSent:
    """The size gate must weigh the bytes the server receives, not a shorter form.

    SMTP ends every line with CRLF; `EmailMessage.as_bytes()` does not. Measuring
    with `as_bytes()` understated a 20 MB message by 1.3% -- enough that a message
    inside the gate could still be refused by the server for being too large,
    which is the one outcome the gate exists to prevent.
    """

    def test_the_message_goes_out_with_crlf_line_endings(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message(body="line one\nline two"))
        payload = fake_smtp.last_payload
        assert b"\r\n" in payload
        assert payload.replace(b"\r\n", b"") .count(b"\n") == 0

    def test_the_sent_payload_is_longer_than_its_own_lf_form(self, fake_smtp):
        # The gate weighs this payload. Had it weighed the LF form -- which is
        # what as_bytes() produces -- it would have understated by exactly the
        # number of lines, and let through a message the server counts as bigger.
        Mailer(ACCOUNT).send(make_message(body="\n".join(["line"] * 100)))
        payload = fake_smtp.last_payload
        lf_form = payload.replace(b"\r\n", b"\n")
        assert len(payload) > len(lf_form)

    def test_the_wire_form_is_larger_than_as_bytes_and_that_is_the_point(self):
        # If these ever match, the gate could go back to as_bytes() -- but they
        # do not, one byte per line.
        many_lines = make_message(body="\n".join(["line"] * 100))
        mime = many_lines.to_mime(sender="me@example.com")
        assert len(_as_wire_bytes(mime)) > len(mime.as_bytes())


class TestEnvelope:
    def test_bcc_reaches_the_server_without_appearing_in_the_message(self, fake_smtp):
        Mailer(ACCOUNT).send(
            make_message(to="lead@example.com", bcc="audit@example.com")
        )
        sent = fake_smtp.sent_messages[0]
        assert "audit@example.com" in sent.recipients
        assert "audit@example.com" not in sent.payload.decode()

    def test_envelope_carries_to_cc_and_bcc_alike(self, fake_smtp):
        Mailer(ACCOUNT).send(
            make_message(
                to="lead@example.com",
                cc="analyst@example.com",
                bcc="audit@example.com",
            )
        )
        assert fake_smtp.sent_messages[0].recipients == [
            "lead@example.com",
            "analyst@example.com",
            "audit@example.com",
        ]

    def test_envelope_sender_is_the_account(self, fake_smtp):
        Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.sent_messages[0].sender == "sender@example.com"


class TestReceipt:
    def test_clean_send_reports_every_recipient_accepted(self, fake_smtp):
        receipt = Mailer(ACCOUNT).send(make_message(to="lead@example.com"))
        assert receipt.is_complete
        assert receipt.accepted == ("lead@example.com",)
        assert receipt.reason_by_refused_recipient == {}

    def test_receipt_carries_the_message_id(self, fake_smtp):
        receipt = Mailer(ACCOUNT).send(make_message())
        assert receipt.message_id.endswith("@example.com>")

    def test_partial_refusal_is_reported_not_raised(self, fake_smtp):
        fake_smtp.refusals = {"typo@example.com": (550, b"No such user")}
        receipt = Mailer(ACCOUNT).send(
            make_message(to=["lead@example.com", "typo@example.com"])
        )
        assert not receipt.is_complete
        assert receipt.accepted == ("lead@example.com",)
        assert "550" in receipt.reason_by_refused_recipient["typo@example.com"]

    def test_every_recipient_refused_raises(self, fake_smtp):
        fake_smtp.refusals = {"lead@example.com": (550, b"No such user")}
        with pytest.raises(RecipientRefusedError, match="nothing was sent"):
            Mailer(ACCOUNT).send(make_message(to="lead@example.com"))


class TestChecksRunBeforeConnecting:
    """A message the provider would reject never reaches the network."""

    def test_blocked_attachment_is_caught_without_opening_a_connection(
        self, fake_smtp, tmp_path
    ):
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"MZ")
        with pytest.raises(BlockedAttachmentError):
            Mailer(ACCOUNT).send(
                make_message(attachments=(Attachment.from_path(installer),))
            )
        assert fake_smtp.connections == []

    def test_oversized_message_is_caught_without_opening_a_connection(self, fake_smtp):
        with pytest.raises(MessageTooLargeError):
            Mailer(TINY_LIMIT_ACCOUNT).send(make_message())
        assert fake_smtp.connections == []


class TestServerAdvertisedLimit:
    """The server's own SIZE beats the provider constant, which can go stale."""

    def test_message_the_server_says_is_too_big_is_refused_before_sending(
        self, fake_smtp
    ):
        fake_smtp.esmtp_features = {"size": "10"}  # server accepts 10 bytes
        with pytest.raises(MessageTooLargeError):
            Mailer(ACCOUNT).send(make_message())
        assert fake_smtp.sent_messages == []

    def test_server_that_advertises_no_size_falls_back_to_the_constant(
        self, fake_smtp
    ):
        fake_smtp.esmtp_features = {}
        receipt = Mailer(ACCOUNT).send(make_message())
        assert receipt.is_complete

    def test_unparseable_advertised_size_falls_back_to_the_constant(self, fake_smtp):
        fake_smtp.esmtp_features = {"size": "unlimited"}
        receipt = Mailer(ACCOUNT).send(make_message())
        assert receipt.is_complete


class TestTooLargeIsRefusedWithoutReadingTheFile:
    """The refusal used to cost more memory than the message.

    `send` weighed only the finished article: to_mime read every attachment in,
    flattening laid a base64 copy beside it, and *then* the size was checked. A
    200 MiB attachment took ~1.5 GB of resident memory to earn "too large" --
    an answer `size_bytes` had implied since construction. Big enough, and the
    OOM killer answers first: SIGKILL, no MessageTooLargeError to catch.
    """

    def test_an_oversized_attachment_is_refused_before_it_is_read(
        self, fake_smtp, tmp_path, monkeypatch
    ):
        big = tmp_path / "big.bin"
        big.write_bytes(b"\0" * 4096)

        def fail_if_read(self, *args, **kwargs):
            raise AssertionError("the file was read to find out it was too large")

        monkeypatch.setattr(Path, "read_bytes", fail_if_read)
        message = make_message(attachments=[Attachment.from_path(big)])

        with pytest.raises(MessageTooLargeError):
            Mailer(TINY_LIMIT_ACCOUNT).send(message)

    def test_a_message_under_the_limit_is_not_refused_by_the_estimate(
        self, fake_smtp, tmp_path
    ):
        """The half that matters more: the cheap gate must never false-refuse.

        It underestimates on purpose -- attachments only, at a ratio just under
        the true one -- so anything it stops, the exact check would stop too. A
        gate that guessed high would refuse mail the provider would have taken,
        which is worse than the cost it saves.
        """
        attachment = tmp_path / "report.bin"
        attachment.write_bytes(b"\0" * 1024)
        message = make_message(attachments=[Attachment.from_path(attachment)])

        receipt = Mailer(ACCOUNT).send(message)  # Gmail's real limit

        assert receipt.is_complete

    def test_the_estimate_stays_under_the_real_wire_size(self, tmp_path):
        """Why the above holds, pinned rather than trusted.

        If this inverts, the cheap gate starts refusing messages the server
        would accept, and it does so before anything can weigh them for real.
        """
        for size in (1024, 64 * 1024, 1024 * 1024):
            attachment = tmp_path / f"{size}.bin"
            attachment.write_bytes(b"\0" * size)
            message = make_message(attachments=[Attachment.from_path(attachment)])
            estimate = estimated_encoded_bytes(message.attachments)
            actual = len(_as_wire_bytes(message.to_mime(sender="me@example.com")))
            assert estimate < actual, f"estimate {estimate} >= actual {actual}"


SSL_PROVIDER = MailProvider(
    name               = "sslmail",
    smtp_host          = "smtp.example.com",
    smtp_port          = 465,
    security           = "ssl",
    blocked_extensions = GMAIL.blocked_extensions,
    max_message_bytes  = GMAIL.max_message_bytes,
    login_requirements = GMAIL.login_requirements,
)
SSL_ACCOUNT = SmtpAccount(
    name="sslmail", username="sender@example.com", provider=SSL_PROVIDER
)


class TestAProviderThatWantsTlsFromTheFirstByte:
    """`security="ssl"` is a public choice, and had never once been executed.

    Both shipped providers use STARTTLS, so nothing reached this branch -- not a
    test, not a send. `SmtpSecurity` is `Literal["starttls", "ssl"]`, so a caller
    can build one; the code just had no evidence it worked.
    """

    @pytest.fixture
    def fake_ssl_smtp(self, fake_smtp, monkeypatch):
        def connect(host, port, timeout=None, context=None):
            fake_smtp.connections.append(
                {"host": host, "port": port, "timeout": timeout, "context": context}
            )
            return fake_smtp

        monkeypatch.setattr("mailrun.mailer.smtplib.SMTP_SSL", connect)
        store_password(SSL_ACCOUNT, "app-pw")
        return fake_smtp

    def test_it_connects_with_smtp_ssl(self, fake_ssl_smtp):
        Mailer(SSL_ACCOUNT).send(make_message())
        assert fake_ssl_smtp.connections[0]["host"] == "smtp.example.com"
        assert fake_ssl_smtp.connections[0]["port"] == 465

    def test_it_does_not_also_start_tls(self, fake_ssl_smtp):
        # The socket is already encrypted; asking again is a protocol error.
        Mailer(SSL_ACCOUNT).send(make_message())
        assert not fake_ssl_smtp.started_tls

    def test_it_checks_the_certificate_too(self, fake_ssl_smtp):
        """The hole this branch shared with the other one.

        `SMTP_SSL` takes the same unverified default as `starttls`, so an
        impostor answering on 465 was handed the password just as readily.
        """
        Mailer(SSL_ACCOUNT).send(make_message())
        context = fake_ssl_smtp.connections[0]["context"]
        assert context is not None, "no context: smtplib would verify nothing"
        assert context.check_hostname is True
        assert context.verify_mode is ssl.CERT_REQUIRED

    def test_it_still_sends(self, fake_ssl_smtp):
        receipt = Mailer(SSL_ACCOUNT).send(make_message())
        assert receipt.is_complete
