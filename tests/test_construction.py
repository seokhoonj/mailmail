"""Records that hold two strings must be built by naming them.

`SmtpAccount("me@example.com", "gmail", NAVER)` used to be accepted, and the
account came out believing its name was an address and its username was
"gmail" -- no error, wrong everything, and only visible on the failed login. The
same held for `Message("body", "subject", ...)`: two strings, positional, silently
swapped. Nothing catches this at runtime, because both orderings are perfectly
typed.

`kw_only=True` is one word per class and easy to drop in a refactor, so what it
buys is pinned here rather than trusted.
"""

import pytest

from mailrun import MailProvider, Message, SmtpAccount
from mailrun.provider import NAVER


class TestTheseCannotBeMisordered:
    def test_an_account_cannot_be_built_positionally(self):
        with pytest.raises(TypeError):
            SmtpAccount("me@example.com", "gmail", NAVER)  # type: ignore[call-arg]

    def test_a_message_cannot_be_built_positionally(self):
        with pytest.raises(TypeError):
            Message("body", "subject", ("lead@example.com",))  # type: ignore[call-arg]

    def test_a_provider_cannot_be_built_positionally(self):
        with pytest.raises(TypeError):
            MailProvider(  # type: ignore[call-arg]
                "naver", "smtp.naver.com", 587, "starttls", frozenset(), 1, "n/a"
            )


class TestNamingThemStillWorks:
    def test_an_account_reads_as_it_was_written(self):
        account = SmtpAccount(name="naver", username="me@naver.com", provider=NAVER)
        assert account.name == "naver"
        assert account.username == "me@naver.com"

    def test_a_message_reads_as_it_was_written(self):
        message = Message(subject="Weekly report", body="FYI", to=("lead@example.com",))
        assert message.subject == "Weekly report"
        assert message.body == "FYI"
