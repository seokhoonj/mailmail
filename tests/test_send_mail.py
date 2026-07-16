"""The public one-liner: which recipients a message actually goes to.

Configured defaults fire on omission, which is convenient and sharp-edged in
equal measure. These tests pin the one rule that keeps it safe: only `None`
reaches the default, and `()` always means nobody.
"""

import pytest

from mailrun import Config, InvalidMessageError, SmtpAccount, send_mail
from mailrun.credentials import store_password
from mailrun.provider import NAVER

ACCOUNT = SmtpAccount(name="naver", username="me@example.com", provider=NAVER)


@pytest.fixture(autouse=True)
def _password_on_file(fake_smtp):
    """Every test here sends, and sending needs a password stored."""
    store_password(ACCOUNT, "app-pw")

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


def headers_of(server):
    """The To/Cc the server was actually handed."""
    return server.header("To"), server.header("Cc")


class TestWithoutConfiguredDefaults:
    def test_named_recipient_is_used(self, fake_smtp):
        send_mail(to="lead", subject="s", body="b", config=a_config())
        assert fake_smtp.sent_messages[0].recipients == ["lead@example.com"]

    def test_message_with_no_recipient_at_all_is_refused(self, fake_smtp):
        with pytest.raises(ValueError, match="at least one recipient"):
            send_mail(subject="s", body="b", config=a_config())
        assert fake_smtp.sent_messages == []


class TestConfiguredDefaults:
    """to and cc default to the configured recipients when not mentioned."""

    def config_with_defaults(self):
        return a_config(default_to=("lead",), default_cc=("reviewer",))

    def test_omitting_everything_uses_both_defaults(self, fake_smtp):
        send_mail(subject="s", body="b", config=self.config_with_defaults())
        assert fake_smtp.sent_messages[0].recipients == [
            "lead@example.com",
            "reviewer@example.com",
        ]
        assert headers_of(fake_smtp) == ("lead@example.com", "reviewer@example.com")

    def test_default_recipients_are_resolved_through_the_address_book(
        self, fake_smtp
    ):
        # The default is stored as an alias, not an address; it must expand.
        send_mail(subject="s", body="b", config=self.config_with_defaults())
        assert "lead@example.com" in fake_smtp.sent_messages[0].recipients

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
        assert fake_smtp.sent_messages[0].recipients == ["me@example.com"]

    def test_empty_to_with_a_named_cc_still_delivers_to_the_cc(self, fake_smtp):
        send_mail(
            to=(), cc="lead", subject="s", body="b", config=self.config_with_defaults()
        )
        assert fake_smtp.sent_messages[0].recipients == ["lead@example.com"]

    def test_emptying_every_recipient_is_refused(self, fake_smtp):
        with pytest.raises(ValueError, match="at least one recipient"):
            send_mail(
                to=(), cc=(), subject="s", body="b", config=self.config_with_defaults()
            )

    # The whole matrix, not a sample: this is the mechanism that decides whether
    # a note meant for one person quietly carries a cc, so every cell is pinned.
    # UNSET is the sentinel for "the argument was not passed at all", which is
    # the case that reaches the default -- distinct from passing None.
    UNSET = object()

    @pytest.mark.parametrize(
        ("given_to", "given_cc", "expected_to", "expected_cc"),
        [
            # to omitted -> default; cc omitted -> default
            (UNSET, UNSET, "lead@example.com",     "reviewer@example.com"),
            # to named -> named; cc omitted -> default (the sharp edge)
            ("me",  UNSET,  "me@example.com",      "reviewer@example.com"),
            # to emptied -> nobody; cc omitted -> default carries the message
            ((),    UNSET,  None,                  "reviewer@example.com"),
            # to omitted -> default; cc named -> named
            (UNSET, "me",   "lead@example.com",    "me@example.com"),
            ("me",  "lead", "me@example.com",      "lead@example.com"),
            ((),    "lead", None,                  "lead@example.com"),
            # cc emptied -> nobody, whatever to does
            (UNSET, (),     "lead@example.com",    None),
            ("me",  (),     "me@example.com",      None),
        ],
    )
    def test_every_combination_of_to_and_cc(
        self, fake_smtp, given_to, given_cc, expected_to, expected_cc
    ):
        named = {}
        if given_to is not self.UNSET:
            named["to"] = given_to
        if given_cc is not self.UNSET:
            named["cc"] = given_cc

        send_mail(subject="s", body="b", config=self.config_with_defaults(), **named)

        assert headers_of(fake_smtp) == (expected_to, expected_cc)

    def test_emptying_both_is_the_one_combination_that_cannot_send(self, fake_smtp):
        with pytest.raises(InvalidMessageError, match="at least one recipient"):
            send_mail(
                to=(), cc=(), subject="s", body="b", config=self.config_with_defaults()
            )
        assert fake_smtp.sent_messages == []

    def test_default_bcc_is_delivered_without_a_header(self, fake_smtp):
        send_mail(
            subject="s",
            body="b",
            config=a_config(default_to=("lead",), default_bcc=("reviewer",)),
        )
        assert "reviewer@example.com" in fake_smtp.sent_messages[0].recipients
        assert "reviewer@example.com" not in fake_smtp.last_payload.decode()

    def test_bcc_default_can_be_emptied_too(self, fake_smtp):
        send_mail(
            bcc=(),
            subject="s",
            body="b",
            config=a_config(default_to=("lead",), default_bcc=("reviewer",)),
        )
        assert fake_smtp.sent_messages[0].recipients == ["lead@example.com"]
