"""Claims the docstrings make, checked against the code that has to keep them.

Prose is the one thing here nothing was watching. pytest, ruff and mypy all pass
over a sentence that stopped being true, which is how two of these shipped in a
single afternoon: `to` lost its fallback and the module docstring went on
promising one; `UnscannableArchiveError` was added and `check_attachments`'
Raises section never heard. Both were caught by a human reading, which does not
scale and did not catch the third -- errors.py telling every caller that one
`except MailmailError` guards a whole send, while `Mailer.send` documented
raising `smtplib.SMTPException` two files away.

These do not check style or wording. Each one names a specific claim and asks
the code whether it is still true.
"""

import builtins
import inspect
import re
import smtplib
import ssl

import pytest

import mailmail
from mailmail import Message
from mailmail.attachment import check_attachments
from mailmail.errors import MailmailError


class TestTheModuleDocstringDescribesTodaysApi:
    def test_it_does_not_promise_a_default_recipient(self):
        """`[defaults]` is gone and `to` is required; `help(mailmail)` is the
        first place anyone reads otherwise."""
        assert mailmail.__doc__ is not None
        assert "falls back to the configured default" not in mailmail.__doc__

    def test_it_says_to_is_required(self):
        assert mailmail.__doc__ is not None
        assert "required" in mailmail.__doc__

    def test_the_signature_agrees(self):
        """The claim above is only worth making if the code still enforces it."""
        parameter = inspect.signature(mailmail.send).parameters["to"]
        assert parameter.default is inspect.Parameter.empty


class TestTheErrorsModuleIsHonestAboutItsReach:
    """It used to say `MailmailError` caught everything a send could raise."""

    def test_it_does_not_claim_to_catch_every_failure(self):
        assert mailmail.errors.__doc__ is not None
        assert "guard a whole send with one" not in mailmail.errors.__doc__

    def test_it_names_what_passes_through_untranslated(self):
        assert mailmail.errors.__doc__ is not None
        for escaping in ("smtplib.SMTPException", "OSError"):
            assert escaping in mailmail.errors.__doc__

    @pytest.mark.parametrize("escaping", [smtplib.SMTPException, OSError])
    def test_those_really_do_escape_the_hierarchy(self, escaping):
        """Pins why the sentence is worded that way. If either of these ever
        became a MailmailError, the docstring would be wrong in the other
        direction."""
        assert not issubclass(escaping, MailmailError)


# The two functions a caller actually reaches for. Every claim below is asked of
# both, because the first cut of this file asked only the places already fixed --
# `check_attachments` and the errors module -- and passed while `send`, the
# most-read docstring in the package, still carried the promise the review had
# just disproved. A gate drawn around the repair is not a gate.
PUBLIC_SEND_APIS = [mailmail.send, mailmail.Mailer.send]


class TestDocumentedRaisesMatchWhatIsRaised:
    def test_check_attachments_names_the_unscannable_case(self):
        """It raises UnscannableArchiveError -- for a deep nest, an oversized
        member, or a corrupt archive -- and said nothing about it."""
        assert check_attachments.__doc__ is not None
        assert "UnscannableArchiveError" in check_attachments.__doc__

    @pytest.mark.parametrize("api", PUBLIC_SEND_APIS)
    def test_the_public_send_apis_name_it_too(self, api):
        """`check_attachments` is not what anyone calls. These are, and the error
        propagates through both."""
        doc = inspect.getdoc(api)
        assert doc is not None
        assert "UnscannableArchiveError" in doc

    @pytest.mark.parametrize("api", PUBLIC_SEND_APIS)
    def test_they_do_not_claim_mailmail_error_catches_the_network(self, api):
        doc = inspect.getdoc(api)
        assert doc is not None
        assert "guards the whole send" not in doc

    @pytest.mark.parametrize("api", PUBLIC_SEND_APIS)
    def test_they_name_what_escapes_the_hierarchy(self, api):
        doc = inspect.getdoc(api)
        assert doc is not None
        for escaping in ("smtplib.SMTPException", "OSError"):
            assert escaping in doc

    def test_every_error_the_public_api_documents_actually_exists(self):
        """A Raises section naming an exception the package does not export is a
        reader sent looking for something that is not there."""
        documented: set[str] = set()
        for name in mailmail.__all__:
            member = getattr(mailmail, name)
            doc = inspect.getdoc(member)
            if not doc or "Raises" not in doc:
                continue
            documented.update(re.findall(r"\b([A-Z]\w+Error)\b", doc))
        # A documented name is fine if the reader can reach it: from mailmail, or
        # from a stdlib module the docstring names beside it.
        reachable = (mailmail, builtins, smtplib, ssl)
        missing = [
            name
            for name in documented
            if not any(hasattr(module, name) for module in reachable)
        ]
        assert not missing, f"documented but not exported: {sorted(missing)}"


class TestMessageDocstringDescribesTheStrictnessItHas:
    def test_it_does_not_claim_more_strictness_than_the_code_has(self):
        """"The constructor is strict" read as "anything but a tuple raises".

        It refuses `str` and normalises every other iterable, which is right --
        a stored list would break both the frozen hash and `self.to + self.cc`
        -- but a reader who took the sentence literally would expect a list to
        raise.
        """
        assert Message(subject="s", body="b", to=["a@b.com"]).to == ("a@b.com",)  # type: ignore[arg-type]
        assert Message.__doc__ is not None
        assert "strict where it counts" in Message.__doc__
