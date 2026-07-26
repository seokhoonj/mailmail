"""One fake SMTP server, shared by every test that sends.

There used to be two, one per test module, and they drifted: the package moved
from `send_message` to `sendmail` and only one fake knew. A fake that disagrees
with the real client's contract lets the suite pass while the send is broken,
which is the failure mode a fake exists to avoid. One fake, one place to keep
honest.

It mirrors `smtplib.SMTP` only where mailmail touches it -- `ehlo`, `starttls`,
`login`, `sendmail`, `quit`, `close`, and `esmtp_features`. Each raise is
injectable, because the paths worth testing here are the ones where something
goes wrong partway through a handshake.
"""

import email
import os
import smtplib
from pathlib import Path
from typing import NamedTuple

import pytest


@pytest.fixture
def unresolvable_tilde(monkeypatch):
    """A `~user` value that deterministically fails to expand.

    `Path.expanduser` raises RuntimeError for a `~user` naming nobody, which is
    how the package's `~user` guards are reached. Relying on a real absent
    account makes the test depend on the host user database; instead, force the
    raise for one sentinel prefix -- and only that prefix, so `Path.home()` (which
    expands a bare `~`) still works. Returns the sentinel value to feed in.
    """
    real_expanduser = Path.expanduser

    def fake_expanduser(self):
        if str(self).startswith("~nouser_sentinel"):
            raise RuntimeError("Could not determine home directory")
        return real_expanduser(self)

    monkeypatch.setattr(Path, "expanduser", fake_expanduser)
    return "~nouser_sentinel"

# What smtp.gmail.com really advertises, so the default case is the real one.
GMAIL_ADVERTISED_SIZE = 35_882_577


class SentMessage(NamedTuple):
    """One handoff to the server, as the server saw it.

    A record rather than a dict so `last_payload` can honestly promise bytes --
    out of an untyped dict it was promising `Any` and calling it `bytes`, which
    is the same lying-hint the package itself was just fixed for.
    """

    payload: bytes
    sender: str
    recipients: list[str]


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
        self.sent_messages: list[SentMessage] = []
        self.logins: list[tuple[str, str]] = []
        self.connections: list[dict[str, object]] = []
        self.started_tls = False
        self.starttls_context = None
        self.quit_count = 0
        self.close_count = 0
        self.rejects_login = False
        self.starttls_raises = None
        self.login_raises = None
        self.quit_raises = None
        self.sendmail_raises = None
        self.sendmail_raises_on = None
        self.refusals = refusals or {}

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, context=None):
        # The context is kept, not ignored: passing one that verifies is the
        # whole defence against handing the password to an impostor, and a fake
        # that swallowed the argument would let it go missing unnoticed.
        self.starttls_context = context
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
        # A transport failure (the server drops the connection mid-batch) raises
        # before anything is recorded, and can be aimed at one recipient so a
        # batch test can fail on a chosen row and check the rest never went.
        if self.sendmail_raises is not None and (
            self.sendmail_raises_on is None or self.sendmail_raises_on in to_addrs
        ):
            raise self.sendmail_raises
        # Real smtplib reports a refusal only for a recipient it was asked to
        # deliver to, and raises SMTPRecipientsRefused only when the refusals
        # cover them all -- during RCPT, before DATA, so a totally refused message
        # never reaches the wire. Returning the whole refusals map for every send,
        # or recording a totally refused message as sent, is invisible to a
        # single-message test and wrong for a batch.
        requested = set(to_addrs)
        refused = {
            address: reply
            for address, reply in self.refusals.items()
            if address in requested
        }
        if refused and set(refused) >= requested:
            raise smtplib.SMTPRecipientsRefused(refused)
        self.sent_messages.append(
            SentMessage(payload=msg, sender=from_addr, recipients=list(to_addrs))
        )
        return refused

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
        return self.sent_messages[0].payload

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

    monkeypatch.setattr("mailmail.mailer.smtplib.SMTP", fake_smtp_class)
    monkeypatch.setenv("MAILMAIL_CREDENTIALS", str(tmp_path / "credentials.json"))
    # Clear the bare and every per-account password var, so the operator's real
    # environment cannot leak a password into a test.
    for key in [k for k in os.environ if k.startswith("MAILMAIL_PASSWORD")]:
        monkeypatch.delenv(key, raising=False)
    return server
