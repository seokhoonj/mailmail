"""The MIME a message assembles into -- headers, parts, and what stays hidden."""

import pytest

from mailmail.attachment import Attachment
from mailmail.errors import InvalidMessageError, MailmailError
from mailmail.message import Message

SENDER = "sender@example.com"


def _make_message(**overrides):
    fields = {
        "subject": "Weekly report",
        "body":    "Please see attached.",
        "to":      "lead@example.com",
    }
    return Message.compose(**(fields | overrides))


class TestRecipientNormalization:
    def test_single_address_string_becomes_one_recipient(self):
        assert _make_message(to="lead@example.com").to == ("lead@example.com",)

    def test_string_is_not_exploded_into_characters(self):
        # A str is itself iterable; without normalization this is the bug where
        # "lead@example.com" becomes 16 single-character recipients.
        assert len(_make_message(to="lead@example.com").to) == 1

    def test_list_of_addresses_is_kept_in_order(self):
        message = _make_message(to=["lead@example.com", "analyst@example.com"])
        assert message.to == ("lead@example.com", "analyst@example.com")

    def test_recipients_span_to_cc_and_bcc(self):
        message = _make_message(
            to="lead@example.com", cc="analyst@example.com", bcc="audit@example.com"
        )
        assert message.recipients == (
            "lead@example.com",
            "analyst@example.com",
            "audit@example.com",
        )

    def test_message_without_recipients_is_rejected(self):
        with pytest.raises(InvalidMessageError, match="at least one recipient"):
            _make_message(to=())

    def test_message_without_a_subject_is_rejected(self):
        with pytest.raises(InvalidMessageError, match="subject"):
            _make_message(subject="   ")


class TestOneExceptGuardsTheWholeSend:
    """errors.py promises every error descends from MailmailError. It must.

    These two used to escape as a bare ValueError -- and the skill *instructs*
    the caller to pass `cc=()`, so the shipped consumer walked at the one path
    the promise did not cover.
    """

    def test_a_message_with_no_recipient_raises_a_mailmail_error(self):
        with pytest.raises(MailmailError):
            _make_message(to=())

    def test_a_message_with_no_subject_raises_a_mailmail_error(self):
        with pytest.raises(MailmailError):
            _make_message(subject="")

    def test_it_is_still_a_value_error_for_anyone_who_caught_that(self):
        # Both, deliberately: a bad argument has always been a ValueError, and
        # nobody who reasonably wrote `except ValueError` should be broken.
        assert issubclass(InvalidMessageError, ValueError)
        assert issubclass(InvalidMessageError, MailmailError)


class TestAddressesWithLineBreaks:
    """A line break in an address dies here, not on an open connection.

    `to` and `cc` are checked for free when they become headers. `bcc` never
    becomes one, so nothing looked at it until smtplib refused it -- with the
    connection open and the login already spent, from a package whose whole claim
    is that rejects land before it dials.
    """

    @pytest.mark.parametrize("field", ["to", "cc", "bcc"])
    def test_a_line_break_in_any_recipient_field_is_refused(self, field):
        crafted = "audit@example.com>\r\nRCPT TO:<attacker@example.net"
        with pytest.raises(InvalidMessageError, match="line break"):
            _make_message(**{field: crafted})

    def test_a_bare_newline_is_refused_too(self):
        with pytest.raises(InvalidMessageError, match="line break"):
            _make_message(bcc="audit@example.com\nBcc: attacker@example.net")

    def test_the_refusal_happens_before_any_mime_is_built(self):
        # __post_init__, not to_mime: nothing should get as far as assembling.
        with pytest.raises(InvalidMessageError):
            Message.compose(
                subject="s", body="b", to="a@example.com", bcc="b@example.com\r\n"
            )


class TestSubjectWithLineBreaks:
    """A line break in the subject dies here too: it would break the Subject
    header, or inject another one (a Bcc, say)."""

    def test_a_line_break_in_the_subject_is_refused(self):
        crafted = "Report\r\nBcc: attacker@example.net"
        with pytest.raises(InvalidMessageError, match="line break"):
            _make_message(subject=crafted)

    def test_a_bare_newline_in_the_subject_is_refused_too(self):
        with pytest.raises(InvalidMessageError, match="line break"):
            _make_message(subject="Report\nBcc: attacker@example.net")


class TestContentThatCannotBeEncoded:
    """A lone surrogate (from an os.fsdecode / surrogateescape decode) is not
    sendable; it dies here, not as a bare UnicodeEncodeError at flatten."""

    def test_a_surrogate_in_the_body_is_refused(self):
        with pytest.raises(InvalidMessageError, match="surrogate"):
            _make_message(body="hi \udce9")

    def test_a_surrogate_in_the_html_is_refused(self):
        with pytest.raises(InvalidMessageError, match="surrogate"):
            _make_message(body="hi", html="<p>\udce9</p>")


class TestHeaders:
    def test_from_and_to_are_set_from_the_sender_and_recipients(self):
        mime = _make_message(to=["lead@example.com", "analyst@example.com"]).to_mime(
            sender=SENDER
        )
        assert mime["From"] == SENDER
        assert mime["To"] == "lead@example.com, analyst@example.com"

    def test_cc_is_written_as_a_header(self):
        mime = _make_message(cc="analyst@example.com").to_mime(sender=SENDER)
        assert mime["Cc"] == "analyst@example.com"

    def test_a_message_addressed_only_to_a_cc_has_no_to_header(self):
        # Not an empty one: `To:` with nothing after it is a malformed header,
        # and some filters read it as a spam signal.
        mime = _make_message(to=(), cc="analyst@example.com").to_mime(sender=SENDER)
        assert mime["To"] is None
        assert mime["Cc"] == "analyst@example.com"

    def test_a_message_with_no_cc_has_no_cc_header(self):
        mime = _make_message(cc=()).to_mime(sender=SENDER)
        assert mime["Cc"] is None

    def test_bcc_is_never_written_as_a_header(self):
        # Writing it would show every blind recipient to all the others.
        message = _make_message(bcc="audit@example.com")
        mime = message.to_mime(sender=SENDER)
        assert mime["Bcc"] is None
        assert "audit@example.com" not in mime.as_string()
        assert "audit@example.com" in message.recipients

    def test_message_id_is_stamped_with_the_sender_domain_not_the_hostname(self):
        mime = _make_message().to_mime(sender=SENDER)
        assert mime["Message-ID"].endswith("@example.com>")

    def test_date_header_is_present(self):
        assert _make_message().to_mime(sender=SENDER)["Date"] is not None


class TestKoreanText:
    """Non-ASCII needs no hand-rolled encoding; the stdlib handles it."""

    def test_korean_subject_survives_a_round_trip(self):
        mime = _make_message(subject="주간 보고서").to_mime(sender=SENDER)
        assert mime["Subject"] == "주간 보고서"

    def test_korean_subject_is_not_left_as_raw_bytes_on_the_wire(self):
        mime = _make_message(subject="주간 보고서").to_mime(sender=SENDER)
        assert "주간 보고서" not in mime.as_string()  # encoded per RFC 2047

    def test_korean_body_survives_a_round_trip(self):
        mime = _make_message(body="첨부 파일 확인 부탁드립니다.").to_mime(sender=SENDER)
        assert mime.get_body().get_content().strip() == "첨부 파일 확인 부탁드립니다."


class TestParts:
    def test_plain_body_alone_is_not_multipart(self):
        mime = _make_message().to_mime(sender=SENDER)
        assert not mime.is_multipart()
        assert mime.get_content().strip() == "Please see attached."

    def test_body_is_sent_verbatim_without_newline_rewriting(self):
        mime = _make_message(body="line one\nline two").to_mime(sender=SENDER)
        assert mime.get_content().strip() == "line one\nline two"
        assert "<br>" not in mime.get_content()

    def test_html_is_added_as_an_alternative_with_the_body_as_fallback(self):
        mime = _make_message(
            body="Plain fallback.", html="<p>Rich <b>markup</b>.</p>"
        ).to_mime(sender=SENDER)
        assert mime.get_content_type() == "multipart/alternative"
        assert mime.get_body(("plain",)).get_content().strip() == "Plain fallback."
        assert "<b>markup</b>" in mime.get_body(("html",)).get_content()

    def test_attachment_arrives_with_its_guessed_type_and_filename(self, tmp_path):
        report = tmp_path / "report.pdf"
        report.write_bytes(b"%PDF-1.4 fake")
        mime = _make_message(
            attachments=(Attachment.from_path(report),)
        ).to_mime(sender=SENDER)
        attached = list(mime.iter_attachments())
        assert len(attached) == 1
        assert attached[0].get_content_type() == "application/pdf"
        assert attached[0].get_filename() == "report.pdf"
        assert attached[0].get_payload(decode=True) == b"%PDF-1.4 fake"

    def test_attachment_and_html_coexist(self, tmp_path):
        report = tmp_path / "report.pdf"
        report.write_bytes(b"%PDF-1.4 fake")
        mime = _make_message(
            html="<p>See attached.</p>", attachments=(Attachment.from_path(report),)
        ).to_mime(sender=SENDER)
        assert mime.get_content_type() == "multipart/mixed"
        assert len(list(mime.iter_attachments())) == 1
        assert mime.get_body(("html",)) is not None


class TestTheConstructorRefusesABareString:
    """The guard with a docstring, an exception message, and no test.

    `str` is an iterable of characters, so a `to` that accepts one turns
    `to="lead@example.com"` into sixteen single-character recipients -- silently,
    and sixteen times over the wire. `_refuse_bare_string` exists to stop that,
    and everything explained it except the suite: disabling the guard left all
    241 tests green while `Message(to="lead@example.com").to` had sixteen
    entries. A guard nothing tests is a guard the next reader may delete.
    """

    def test_a_bare_string_recipient_is_refused(self):
        with pytest.raises(InvalidMessageError):
            Message(subject="s", body="b", to="lead@example.com")  # type: ignore[arg-type]

    def test_the_refusal_says_what_would_have_happened(self):
        with pytest.raises(InvalidMessageError) as caught:
            Message(subject="s", body="b", to="lead@example.com")  # type: ignore[arg-type]
        message = str(caught.value)
        assert "per character" in message
        assert "Message.compose" in message  # the door that does take a string

    def test_a_bare_string_cc_is_refused_too(self):
        with pytest.raises(InvalidMessageError):
            Message(subject="s", body="b", to=("a@b.com",), cc="c@d.com")  # type: ignore[arg-type]

    def test_a_bare_string_bcc_is_refused_too(self):
        with pytest.raises(InvalidMessageError):
            Message(subject="s", body="b", to=("a@b.com",), bcc="c@d.com")  # type: ignore[arg-type]

    def test_a_tuple_of_one_is_what_it_wanted(self):
        message = Message(subject="s", body="b", to=("lead@example.com",))
        assert message.to == ("lead@example.com",)

    def test_any_other_iterable_is_normalised_rather_than_refused(self):
        """Strictness is about `str` alone, not about tuples.

        A list is what a caller naturally writes and cannot be silently wrong, so
        it is coerced to the tuple the field promises rather than rejected.
        """
        message = Message(subject="s", body="b", to=["lead@example.com"])  # type: ignore[arg-type]
        assert message.to == ("lead@example.com",)
