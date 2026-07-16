"""The public one-liner: which recipients a message actually goes to.

Configured defaults fire on omission, which is convenient and sharp-edged in
equal measure. These tests pin the one rule that keeps it safe: only `None`
reaches the default, and `()` always means nobody.
"""

import pytest

from mailrun import Config, SmtpAccount, send_mail
from mailrun.provider import NAVER

ACCOUNT = SmtpAccount(name="naver", username="me@example.com", provider=NAVER)

ADDRESS_BOOK = {
    "lead":     ("lead@example.com",),
    "reviewer": ("reviewer@example.com",),
    "me":       ("me@example.com",),
}


def a_config(**overrides):
    fields = {
        "default_account": "naver",
        "account_by_name": {"naver": ACCOUNT},
        "address_book":    ADDRESS_BOOK,
    }
    return Config(**(fields | overrides))


class FakeSmtp:
    def __init__(self):
        self.esmtp_features = {"size": "39845888"}
        self.sent = []

    def ehlo(self):
        return 250, b"ok"

    def starttls(self):
        pass

    def login(self, username, password):
        pass

    def send_message(self, mime, from_addr, to_addrs):
        self.sent.append({"mime": mime, "to": list(to_addrs)})
        return {}

    def quit(self):
        pass


@pytest.fixture
def fake_smtp(monkeypatch):
    server = FakeSmtp()
    monkeypatch.setattr("mailrun.mailer.smtplib.SMTP", lambda *a, **k: server)
    monkeypatch.setenv("MAILRUN_PASSWORD", "app-password")
    return server


def headers_of(server):
    mime = server.sent[0]["mime"]
    return mime["To"], mime["Cc"]


class TestWithoutConfiguredDefaults:
    def test_named_recipient_is_used(self, fake_smtp):
        send_mail(to="lead", subject="s", body="b", config=a_config())
        assert fake_smtp.sent[0]["to"] == ["lead@example.com"]

    def test_message_with_no_recipient_at_all_is_refused(self, fake_smtp):
        with pytest.raises(ValueError, match="at least one recipient"):
            send_mail(subject="s", body="b", config=a_config())
        assert fake_smtp.sent == []


class TestConfiguredDefaults:
    """to and cc default to the configured recipients when not mentioned."""

    def config_with_defaults(self):
        return a_config(default_to=("lead",), default_cc=("reviewer",))

    def test_omitting_everything_uses_both_defaults(self, fake_smtp):
        send_mail(subject="s", body="b", config=self.config_with_defaults())
        assert fake_smtp.sent[0]["to"] == ["lead@example.com", "reviewer@example.com"]
        assert headers_of(fake_smtp) == ("lead@example.com", "reviewer@example.com")

    def test_default_recipients_are_resolved_through_the_address_book(
        self, fake_smtp
    ):
        # The default is stored as an alias, not an address; it must expand.
        send_mail(subject="s", body="b", config=self.config_with_defaults())
        assert "lead@example.com" in fake_smtp.sent[0]["to"]

    def test_naming_to_overrides_the_default_to(self, fake_smtp):
        send_mail(to="me", subject="s", body="b", config=self.config_with_defaults())
        to_header, _cc = headers_of(fake_smtp)
        assert to_header == "me@example.com"

    def test_naming_to_does_not_disturb_the_default_cc(self, fake_smtp):
        # The sharp edge, stated as a test: a note addressed to yourself still
        # carries the configured cc unless you say otherwise.
        send_mail(to="me", subject="s", body="b", config=self.config_with_defaults())
        _to, cc_header = headers_of(fake_smtp)
        assert cc_header == "reviewer@example.com"

    def test_empty_cc_means_nobody_and_beats_the_default(self, fake_smtp):
        send_mail(
            to="me", cc=(), subject="s", body="b", config=self.config_with_defaults()
        )
        _to, cc_header = headers_of(fake_smtp)
        assert cc_header is None
        assert fake_smtp.sent[0]["to"] == ["me@example.com"]

    def test_empty_to_with_a_named_cc_still_delivers_to_the_cc(self, fake_smtp):
        send_mail(
            to=(), cc="lead", subject="s", body="b", config=self.config_with_defaults()
        )
        assert fake_smtp.sent[0]["to"] == ["lead@example.com"]

    def test_emptying_every_recipient_is_refused(self, fake_smtp):
        with pytest.raises(ValueError, match="at least one recipient"):
            send_mail(
                to=(), cc=(), subject="s", body="b", config=self.config_with_defaults()
            )

    def test_default_bcc_is_delivered_without_a_header(self, fake_smtp):
        send_mail(
            subject="s",
            body="b",
            config=a_config(default_to=("lead",), default_bcc=("reviewer",)),
        )
        assert "reviewer@example.com" in fake_smtp.sent[0]["to"]
        assert "reviewer@example.com" not in fake_smtp.sent[0]["mime"].as_string()

    def test_bcc_default_can_be_emptied_too(self, fake_smtp):
        send_mail(
            bcc=(),
            subject="s",
            body="b",
            config=a_config(default_to=("lead",), default_bcc=("reviewer",)),
        )
        assert fake_smtp.sent[0]["to"] == ["lead@example.com"]
