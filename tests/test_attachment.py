"""Provider attachment rules are enforced locally, before anything is sent."""

import tarfile
import zipfile

import pytest

from mailrun.attachment import Attachment, check_attachments, check_message_size
from mailrun.errors import (
    AttachmentError,
    BlockedAttachmentError,
    EncryptedArchiveError,
    MessageTooLargeError,
)
from mailrun.provider import GMAIL, NAVER


def write_file(directory, name, content=b"payload"):
    path = directory / name
    path.write_bytes(content)
    return path


def write_zip(directory, name, member_names):
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_name in member_names:
            archive.writestr(member_name, "payload")
    return path


def write_encrypted_zip(directory, name, member_name="notes.txt"):
    """A zip whose headers say "encrypted", as a real one's do.

    zipfile cannot write encrypted archives, and it recomputes `flag_bits` on
    write, so the bit is set in the file bytes afterwards. It lives in two places
    -- the local file header and the central directory -- and both are set here,
    because a reader may consult either. Verified against an archive from the
    `zip -P` command line tool, which produces `flag_bits == 0x9`: bit 0 for
    encryption, bit 3 for a trailing data descriptor.
    """
    path = write_zip(directory, name, [member_name])
    raw = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        header_start = raw.find(signature)
        assert header_start != -1, f"no {signature!r} header in the test zip"
        raw[header_start + flag_offset] |= 0x1
    path.write_bytes(bytes(raw))
    return path


def write_tar(directory, name, member_names, mode="w"):
    path = directory / name
    with tarfile.open(path, mode) as archive:
        for member_name in member_names:
            member_path = write_file(directory, f"staged-{member_name}")
            archive.add(member_path, arcname=member_name)
    return path


class TestMimeGuessing:
    """The suffix decides the MIME type; an unknown suffix falls back."""

    @pytest.mark.parametrize(
        ("filename", "expected_mime_type"),
        [
            ("report.pdf",  "application/pdf"),
            ("chart.png",   "image/png"),
            ("notes.txt",   "text/plain"),
            ("page.html",   "text/html"),
            ("data.csv",    "text/csv"),
        ],
    )
    def test_known_suffix_gets_its_type(self, tmp_path, filename, expected_mime_type):
        attachment = Attachment.from_path(write_file(tmp_path, filename))
        assert attachment.mime_type == expected_mime_type

    def test_unknown_suffix_falls_back_to_octet_stream(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "model.parquet"))
        assert attachment.mime_type == "application/octet-stream"

    def test_size_is_read_from_disk(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "notes.txt", b"12345"))
        assert attachment.size_bytes == 5

    def test_missing_file_is_reported_at_construction(self, tmp_path):
        with pytest.raises(AttachmentError, match="not found"):
            Attachment.from_path(tmp_path / "absent.pdf")

    def test_directory_is_not_an_attachment(self, tmp_path):
        with pytest.raises(AttachmentError, match="not a regular file"):
            Attachment.from_path(tmp_path)


class TestBlockedFileTypes:
    """Executable types are refused whether bare, zipped, or renamed."""

    def test_ordinary_office_file_is_allowed(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "report.xlsx"))
        check_attachments([attachment], provider=GMAIL)

    @pytest.mark.parametrize(
        "filename",
        ["setup.exe", "macro.vbs", "script.js", "installer.msi", "tool.jar"],
    )
    def test_executable_is_refused(self, tmp_path, filename):
        attachment = Attachment.from_path(write_file(tmp_path, filename))
        with pytest.raises(BlockedAttachmentError):
            check_attachments([attachment], provider=GMAIL)

    def test_suffix_case_does_not_smuggle_it_through(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "Setup.EXE"))
        with pytest.raises(BlockedAttachmentError):
            check_attachments([attachment], provider=GMAIL)

    def test_naver_refuses_the_same_executables_as_gmail(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "setup.exe"))
        with pytest.raises(BlockedAttachmentError):
            check_attachments([attachment], provider=NAVER)

    def test_python_source_is_allowed_though_javascript_is_not(self, tmp_path):
        check_attachments(
            [Attachment.from_path(write_file(tmp_path, "analysis.py"))], provider=GMAIL
        )
        with pytest.raises(BlockedAttachmentError):
            check_attachments(
                [Attachment.from_path(write_file(tmp_path, "bundle.js"))],
                provider=GMAIL,
            )

    def test_error_says_zipping_will_not_help(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "setup.exe"))
        with pytest.raises(BlockedAttachmentError, match="inside archives"):
            check_attachments([attachment], provider=GMAIL)

    def test_one_bad_attachment_condemns_the_whole_message(self, tmp_path):
        allowed = Attachment.from_path(write_file(tmp_path, "report.pdf"))
        blocked = Attachment.from_path(write_file(tmp_path, "setup.exe"))
        with pytest.raises(BlockedAttachmentError):
            check_attachments([allowed, blocked], provider=GMAIL)


class TestArchiveContents:
    """The block reaches inside archives the standard library can read."""

    def test_clean_zip_is_allowed(self, tmp_path):
        archive = write_zip(tmp_path, "reports.zip", ["q1.pdf", "q2.xlsx"])
        check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_zip_hiding_an_executable_is_refused(self, tmp_path):
        archive = write_zip(tmp_path, "bundle.zip", ["readme.txt", "setup.exe"])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_executable_nested_in_a_zip_folder_is_still_found(self, tmp_path):
        archive = write_zip(tmp_path, "bundle.zip", ["tools/bin/setup.exe"])
        with pytest.raises(BlockedAttachmentError):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_clean_tar_is_allowed(self, tmp_path):
        archive = write_tar(tmp_path, "reports.tar", ["q1.pdf"])
        check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_tar_hiding_an_executable_is_refused(self, tmp_path):
        archive = write_tar(tmp_path, "bundle.tar", ["setup.exe"])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_compressed_tar_is_looked_inside_too(self, tmp_path):
        archive = write_tar(tmp_path, "bundle.tar.gz", ["setup.exe"], mode="w:gz")
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_gzipped_executable_is_refused_on_the_name_underneath(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "setup.exe.gz"))
        with pytest.raises(BlockedAttachmentError):
            check_attachments([attachment], provider=GMAIL)

    def test_gzipped_executable_is_reported_once_not_twice(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "setup.exe.gz"))
        with pytest.raises(BlockedAttachmentError) as caught:
            check_attachments([attachment], provider=GMAIL)
        assert str(caught.value).count("setup.exe.gz") == 1

    def test_gzipped_ordinary_file_is_allowed(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "report.pdf.gz"))
        check_attachments([attachment], provider=GMAIL)

    def test_password_protected_zip_is_refused_unopened(self, tmp_path):
        archive = write_encrypted_zip(tmp_path, "secret.zip")
        with pytest.raises(EncryptedArchiveError, match="password-protected"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_encrypted_zip_is_refused_on_its_contents_being_unreadable_alone(
        self, tmp_path
    ):
        # Nothing inside is blocked; the refusal is about the provider being
        # unable to look, not about what it would have found.
        archive = write_encrypted_zip(tmp_path, "secret.zip", member_name="notes.txt")
        with pytest.raises(EncryptedArchiveError):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_the_encryption_flag_the_test_sets_is_the_one_a_reader_sees(
        self, tmp_path
    ):
        # Guards the fixture itself: an earlier version set flag_bits on an
        # in-memory ZipInfo, which zipfile silently discarded on write, so the
        # test passed a plain zip off as an encrypted one.
        archive = write_encrypted_zip(tmp_path, "secret.zip")
        with zipfile.ZipFile(archive) as opened:
            assert all(member.flag_bits & 0x1 for member in opened.infolist())

    def test_office_file_is_not_treated_as_an_archive(self, tmp_path):
        # An .xlsx is physically a zip; providers do not scan it as one, and
        # neither do we -- the suffix decides, not the bytes.
        workbook = write_zip(tmp_path, "book.xlsx", ["xl/worksheets/sheet1.xml"])
        check_attachments([Attachment.from_path(workbook)], provider=GMAIL)

    def test_file_named_zip_that_is_not_one_is_judged_on_its_suffix_alone(
        self, tmp_path
    ):
        pretender = write_file(tmp_path, "broken.zip", b"not really a zip")
        check_attachments([Attachment.from_path(pretender)], provider=GMAIL)


class TestMessageSize:
    """The size gate reports the raw budget, not just the encoded overage."""

    def test_message_within_the_limit_passes(self):
        check_message_size(1_000, limit_bytes=35_882_577)

    def test_message_exactly_at_the_limit_passes(self):
        check_message_size(35_882_577, limit_bytes=35_882_577)

    def test_message_one_byte_over_is_refused(self):
        with pytest.raises(MessageTooLargeError):
            check_message_size(35_882_578, limit_bytes=35_882_577)

    def test_error_states_both_the_encoded_size_and_the_raw_budget(self):
        with pytest.raises(MessageTooLargeError) as caught:
            check_message_size(40_000_000, limit_bytes=35_882_577)
        message = str(caught.value)
        assert "38.1 MiB" in message  # what the message actually weighs
        assert "34.2 MiB" in message  # what the server accepts
        assert "25.7 MiB" in message  # the raw-file budget that implies
