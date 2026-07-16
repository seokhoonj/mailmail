"""One fake SMTP server, shared by every test that sends.

There used to be two, one per test module, and they drifted: the package moved
from `send_message` to `sendmail` and only one fake knew. A fake that disagrees
with the real client's contract lets the suite pass while the send is broken,
which is the failure mode a fake exists to avoid. One fake, one place to keep
honest.

It mirrors `smtplib.SMTP` only where mailrun touches it -- `ehlo`, `starttls`,
`login`, `sendmail`, `quit`, `close`, and `esmtp_features`. Each raise is
injectable, because the paths worth testing here are the ones where something
goes wrong partway through a handshake.
"""

import email
import smtplib

import pytest

# What smtp.gmail.com really advertises, so the default case is the real one.
GMAIL_ADVERTISED_SIZE = 35_882_577


class FakeSmtp:
    """Records what a real server would have been told.

    Attributes worth setting in a test
    ----------------------------------
    refusals
        `{address: (code, b"reason")}` the server rejects. When it covers every
        recipient, `sendmail` raises like the real one does.
    rejects_login
        Answer the login with Gmail's real app-password refusal.
    starttls_raises, login_raises, quit_raises
        An exception to raise from that step, for the cleanup paths.
    """

    def __init__(self, *, refusals=None, advertised_size=GMAIL_ADVERTISED_SIZE):
        self.esmtp_features = (
            {"size": str(advertised_size)} if advertised_size is not None else {}
        )
        self.sent_messages = []
        self.logins = []
        self.connections = []
        self.started_tls = False
        self.quit_count = 0
        self.close_count = 0
        self.rejects_login = False
        self.starttls_raises = None
        self.login_raises = None
        self.quit_raises = None
        self.refusals = refusals or {}

    def ehlo(self):
        return 250, b"ok"

    def starttls(self):
        if self.starttls_raises is not None:
            raise self.starttls_raises
        self.started_tls = True

    def login(self, username, password):
        self.logins.append((username, password))
        if self.rejects_login:
            raise smtplib.SMTPAuthenticationError(
                534, b"5.7.9 Application-specific password required.\n5.7.9 Learn more"
            )
        if self.login_raises is not None:
            raise self.login_raises

    def sendmail(self, from_addr, to_addrs, msg):
        self.sent_messages.append(
            {"payload": msg, "from": from_addr, "to": list(to_addrs)}
        )
        if self.refusals and set(self.refusals) >= set(to_addrs):
            raise smtplib.SMTPRecipientsRefused(self.refusals)
        return dict(self.refusals)

    def quit(self):
        self.quit_count += 1
        if self.quit_raises is not None:
            # Like the real thing: QUIT goes out before close(), so a dead
            # connection raises here and never reaches close().
            raise self.quit_raises

    def close(self):
        self.close_count += 1

    # -- what the tests read back ------------------------------------------

    @property
    def last_payload(self) -> bytes:
        return self.sent_messages[0]["payload"]

    def header(self, name: str) -> str | None:
        """One header out of the bytes the server was actually handed.

        Read from the payload rather than from a retained EmailMessage: what the
        server receives is the only thing worth asserting on.
        """
        parsed = email.message_from_bytes(self.last_payload)
        return parsed[name]


@pytest.fixture
def fake_smtp(monkeypatch, tmp_path):
    """Install one FakeSmtp behind smtplib.SMTP and hand it back.

    The credentials file is redirected into tmp_path, so the tests neither read
    nor write the operator's real one.
    """
    server = FakeSmtp()

    def fake_smtp_class(host, port, timeout):
        server.connections.append({"host": host, "port": port, "timeout": timeout})
        return server

    monkeypatch.setattr("mailrun.mailer.smtplib.SMTP", fake_smtp_class)
    monkeypatch.setenv("MAILRUN_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("MAILRUN_PASSWORD", raising=False)
    return server
