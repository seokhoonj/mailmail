"""Claims the docstrings make, checked against the code that has to keep them.

Prose is the one thing here nothing was watching. pytest, ruff and mypy all pass
over a sentence that stopped being true, which is how two of these shipped in a
single afternoon: `to` lost its fallback and the module docstring went on
promising one; `UnscannableArchiveError` was added and `check_attachments`'
Raises section never heard. Both were caught by a human reading, which does not
scale and did not catch the third -- errors.py telling every caller that one
`except MailrunError` guards a whole send, while `Mailer.send` documented
raising `smtplib.SMTPException` two files away.

These do not check style or wording. Each one names a specific claim and asks
the code whether it is still true.
"""

import builtins
import inspect
import re
import smtplib

import pytest

import mailrun
from mailrun import Message
from mailrun.attachment import check_attachments
from mailrun.errors import MailrunError


class TestTheModuleDocstringDescribesTodaysApi:
    def test_it_does_not_promise_a_default_recipient(self):
        """`[defaults]` is gone and `to` is required; `help(mailrun)` is the
        first place anyone reads otherwise."""
        assert mailrun.__doc__ is not None
        assert "falls back to the configured default" not in mailrun.__doc__

    def test_it_says_to_is_required(self):
        assert mailrun.__doc__ is not None
        assert "required" in mailrun.__doc__

    def test_the_signature_agrees(self):
        """The claim above is only worth making if the code still enforces it."""
        parameter = inspect.signature(mailrun.send_mail).parameters["to"]
        assert parameter.default is inspect.Parameter.empty


class TestTheErrorsModuleIsHonestAboutItsReach:
    """It used to say `MailrunError` caught everything a send could raise."""

    def test_it_does_not_claim_to_catch_every_failure(self):
        assert mailrun.errors.__doc__ is not None
        assert "guard a whole send with one" not in mailrun.errors.__doc__

    def test_it_names_what_passes_through_untranslated(self):
        assert mailrun.errors.__doc__ is not None
        for escaping in ("smtplib.SMTPException", "OSError"):
            assert escaping in mailrun.errors.__doc__

    @pytest.mark.parametrize("escaping", [smtplib.SMTPException, OSError])
    def test_those_really_do_escape_the_hierarchy(self, escaping):
        """Pins why the sentence is worded that way. If either of these ever
        became a MailrunError, the docstring would be wrong in the other
        direction."""
        assert not issubclass(escaping, MailrunError)


class TestDocumentedRaisesMatchWhatIsRaised:
    def test_check_attachments_names_the_unscannable_case(self):
        """It raises UnscannableArchiveError -- for a deep nest, an oversized
        member, or a corrupt archive -- and said nothing about it."""
        assert check_attachments.__doc__ is not None
        assert "UnscannableArchiveError" in check_attachments.__doc__

    def test_every_error_the_public_api_documents_actually_exists(self):
        """A Raises section naming an exception the package does not export is a
        reader sent looking for something that is not there."""
        documented: set[str] = set()
        for name in mailrun.__all__:
            member = getattr(mailrun, name)
            doc = inspect.getdoc(member)
            if not doc or "Raises" not in doc:
                continue
            documented.update(re.findall(r"\b([A-Z]\w+Error)\b", doc))
        missing = [
            name
            for name in documented
            if not hasattr(mailrun, name)
            and not hasattr(smtplib, name)
            and not hasattr(builtins, name)  # ValueError, OSError: resolvable already
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
