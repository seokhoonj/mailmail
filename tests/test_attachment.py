"""Provider attachment rules are enforced locally, before anything is sent."""

import re
import tarfile
import zipfile

import pytest

from mailrun.attachment import (
    _MAX_ARCHIVE_DEPTH,
    Attachment,
    check_attachments,
    check_message_size,
)
from mailrun.errors import (
    AttachmentError,
    BlockedAttachmentError,
    EncryptedArchiveError,
    MailrunError,
    MessageTooLargeError,
    UnscannableArchiveError,
)
from mailrun.mailer import _as_wire_bytes
from mailrun.message import Message
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


def write_zip_of_files(directory, name, paths):
    """A zip holding real files from disk, so a member can itself be an archive."""
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        for member_path in paths:
            archive.write(member_path, member_path.name)
    return path


def write_tar_of_files(directory, name, paths, mode="w"):
    path = directory / name
    with tarfile.open(path, mode) as archive:
        for member_path in paths:
            archive.add(member_path, arcname=member_path.name)
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

    def test_error_names_the_archives_that_will_not_help(self, tmp_path):
        # It names .zip and .tar.gz specifically, and does not claim archives in
        # general: .7z and .rar have no stdlib reader and are not looked inside,
        # so "compressing never helps" would be a promise the code cannot keep.
        attachment = Attachment.from_path(write_file(tmp_path, "setup.exe"))
        with pytest.raises(BlockedAttachmentError) as caught:
            check_attachments([attachment], provider=GMAIL)
        message = str(caught.value)
        assert ".zip" in message and ".tar.gz" in message
        assert "inside archives" not in message

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


class TestNamesThatEvadeASuffixMatch:
    """Names that are not `setup.exe` but arrive as one.

    Every case here passed the check before it was written, and Gmail refuses
    them -- the trailing-dot case was confirmed against the live server, which
    answered `552 5.7.0 ... content presents a potential security issue`.
    """

    @pytest.mark.parametrize(
        ("member_name", "why"),
        [
            ("setup.exe.",   "Windows drops the trailing dot when it saves"),
            ("setup.exe ",   "Windows drops the trailing space when it saves"),
            ("setup.exe..",  "several dots go the same way"),
            ("setup.exe. ",  "a dot and a space together"),
            ("SETUP.EXE.",   "case and padding at once"),
        ],
    )
    def test_padded_executable_name_is_still_refused(self, tmp_path, member_name, why):
        archive = write_zip(tmp_path, "bundle.zip", [member_name])
        with pytest.raises(BlockedAttachmentError):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_padding_does_not_make_an_ordinary_file_look_blocked(self, tmp_path):
        archive = write_zip(tmp_path, "reports.zip", ["report.pdf.", "notes.txt "])
        check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_a_name_of_only_padding_is_not_blocked(self, tmp_path):
        archive = write_zip(tmp_path, "odd.zip", ["..."])
        check_attachments([Attachment.from_path(archive)], provider=GMAIL)


class TestNestedArchives:
    """Providers open an archive inside an archive; so does this.

    Stopping at the first level let `outer.zip > inner.zip > setup.exe` through
    the gate and into a bounce -- the exact failure the package exists to
    prevent.
    """

    def test_executable_one_archive_down_is_refused(self, tmp_path):
        inner = write_zip(tmp_path, "inner.zip", ["setup.exe"])
        outer = write_zip_of_files(tmp_path, "outer.zip", [inner])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_executable_two_archives_down_is_refused(self, tmp_path):
        inner = write_zip(tmp_path, "inner.zip", ["setup.exe"])
        middle = write_zip_of_files(tmp_path, "middle.zip", [inner])
        outer = write_zip_of_files(tmp_path, "outer.zip", [middle])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_tar_inside_a_zip_is_looked_inside(self, tmp_path):
        inner = write_tar(tmp_path, "inner.tar", ["setup.exe"])
        outer = write_zip_of_files(tmp_path, "outer.zip", [inner])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_zip_inside_a_tar_is_looked_inside(self, tmp_path):
        inner = write_zip(tmp_path, "inner.zip", ["setup.exe"])
        outer = write_tar_of_files(tmp_path, "outer.tar", [inner])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_clean_nested_archives_are_allowed(self, tmp_path):
        inner = write_zip(tmp_path, "inner.zip", ["q1.pdf"])
        outer = write_zip_of_files(tmp_path, "outer.zip", [inner])
        check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_a_nest_too_deep_to_scan_is_refused_rather_than_waved_through(
        self, tmp_path
    ):
        # Refusing is the honest answer: the point of the check is that nothing
        # unscanned reaches the server, so "too deep to look" cannot mean "fine".
        archive = write_zip(tmp_path, "deep.zip", ["q1.pdf"])
        for level in range(_MAX_ARCHIVE_DEPTH + 1):
            archive = write_zip_of_files(tmp_path, f"wrap{level}.zip", [archive])
        with pytest.raises(UnscannableArchiveError, match="deep"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_a_deep_nest_is_not_reported_as_password_protected(self, tmp_path):
        # It used to raise EncryptedArchiveError, which sent whoever hit it
        # looking for a password that was never there. Nothing here is encrypted.
        archive = write_zip(tmp_path, "deep.zip", ["q1.pdf"])
        for level in range(_MAX_ARCHIVE_DEPTH + 1):
            archive = write_zip_of_files(tmp_path, f"wrap{level}.zip", [archive])
        with pytest.raises(UnscannableArchiveError) as caught:
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)
        assert not isinstance(caught.value, EncryptedArchiveError)
        assert "password" not in str(caught.value)

    def test_an_encrypted_zip_is_still_the_narrower_error(self, tmp_path):
        # Encryption stays its own name -- it is the one unscannable reason the
        # sender can actually do something about.
        archive = write_encrypted_zip(tmp_path, "secret.zip")
        with pytest.raises(EncryptedArchiveError):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_both_reasons_share_one_name_a_caller_can_catch(self, tmp_path):
        assert issubclass(EncryptedArchiveError, UnscannableArchiveError)


class TestArchiveNamesAreNotFilenames:
    """A zip member name has none of a real filename's limits."""

    def test_a_thousand_stacked_gz_suffixes_do_not_exhaust_the_stack(self, tmp_path):
        # The filesystem caps a name at 255 bytes, so this is unreachable as a
        # real file -- but a zip stores names up to 64 KB, and the peel used to
        # recurse once per suffix.
        archive = write_zip(tmp_path, "bundle.zip", ["payload" + ".gz" * 5000])
        check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_a_thousand_stacked_gz_over_an_executable_is_still_refused(self, tmp_path):
        archive = write_zip(tmp_path, "bundle.zip", ["setup.exe" + ".gz" * 5000])
        with pytest.raises(BlockedAttachmentError):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)


class TestUnreadableArchives:
    def test_a_corrupt_tar_is_not_read_as_empty_and_therefore_clean(self, tmp_path):
        # `except tarfile.TarError` was the whole family, so a tar that failed to
        # read produced "no members" -- indistinguishable from "nothing blocked".
        pretender = write_file(tmp_path, "broken.tar", b"not really a tar at all")
        check_attachments([Attachment.from_path(pretender)], provider=GMAIL)

    def test_tar_bz2_hiding_an_executable_is_refused(self, tmp_path):
        archive = write_tar(tmp_path, "bundle.tar.bz2", ["setup.exe"], mode="w:bz2")
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_tbz2_hiding_an_executable_is_refused(self, tmp_path):
        archive = write_tar(tmp_path, "bundle.tbz2", ["setup.exe"], mode="w:bz2")
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_txz_hiding_an_executable_is_refused(self, tmp_path):
        archive = write_tar(tmp_path, "bundle.txz", ["setup.exe"], mode="w:xz")
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(archive)], provider=GMAIL)

    def test_7z_is_not_looked_inside_and_does_not_pretend_to_be(self, tmp_path):
        # A documented limit, pinned so nobody mistakes silence for protection:
        # there is no stdlib reader for .7z, so an executable inside one reaches
        # the server and is refused there. Refusing every .7z locally would be
        # worse -- it would block the innocent ones the provider accepts.
        sevenzip = write_file(tmp_path, "bundle.7z", b"7z\xbc\xaf\x27\x1c fake")
        check_attachments([Attachment.from_path(sevenzip)], provider=GMAIL)


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
        assert "25.0 MiB" in message  # the raw-file budget that implies

    def test_a_file_of_the_advised_size_actually_fits(self, tmp_path):
        """The number in the message is a number a reader can act on.

        The advice used to divide by 4/3, forgetting that base64 breaks the line
        every 76 characters and each line carries a CRLF. It told a caller their
        files could total 25.7 MiB against Gmail when 25.0 is the truth -- so
        believing it earned a bounce, from the one figure in this package whose
        entire job is to prevent one.

        What is tested is what a reader actually reads: the MiB figure in the
        error, not the byte arithmetic behind it.
        """
        limit = GMAIL.max_message_bytes
        with pytest.raises(MessageTooLargeError) as caught:
            check_message_size(limit + 1, limit_bytes=limit)
        advised = re.search(r"total roughly ([\d.]+) MiB", str(caught.value))
        assert advised, f"the error states no budget: {caught.value}"
        advised_mib = float(advised.group(1))

        payload = tmp_path / "big.bin"
        payload.write_bytes(b"\0" * int(advised_mib * 1024 * 1024))
        message = Message.compose(
            subject="s",
            body="b",
            to="lead@example.com",
            attachments=(Attachment.from_path(payload),),
        )
        encoded = len(_as_wire_bytes(message.to_mime(sender="me@example.com")))
        assert encoded <= limit, (
            f"the error advises {advised_mib} MiB of files, which encode to "
            f"{encoded:,} -- past the {limit:,} the server takes"
        )

    def test_the_old_four_thirds_advice_would_not_have_fitted(self, tmp_path):
        # Locks down the bug rather than the fix: the discarded formula is still
        # arithmetic anyone might reach for, so name why it is wrong.
        limit = GMAIL.max_message_bytes
        payload = tmp_path / "old.bin"
        payload.write_bytes(b"\0" * (limit * 3 // 4))
        message = Message.compose(
            subject="s",
            body="b",
            to="lead@example.com",
            attachments=(Attachment.from_path(payload),),
        )
        encoded = len(_as_wire_bytes(message.to_mime(sender="me@example.com")))
        assert encoded > limit


class TestAnArchiveThatCannotBeReadToTheEnd:
    """A scan that stops early must not report what it managed to see.

    Both branches swallowed the failure and returned what they had, which reads
    to the caller as "nothing blocked in here" -- the one answer this package
    exists to be sure about. Two crafted-or-corrupt files got a blocked
    executable past the gate, and the gate said nothing.
    """

    def test_a_corrupt_nested_zip_does_not_hide_what_follows_it(self, tmp_path):
        """One flipped byte used to smuggle setup.exe out.

        `except zipfile.BadZipFile` wrapped the whole member loop, so a bad CRC
        raised while reading member 0 stopped the generator before member 1 --
        the executable -- was ever yielded.
        """
        inner = write_zip(tmp_path, "inner.zip", ["notes.txt"])
        blocked = write_file(tmp_path, "setup.exe", b"MZ")
        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_STORED) as archive:
            archive.write(inner, "inner.zip")  # scanned first, and corrupted below
            archive.write(blocked, "setup.exe")

        whole = bytearray(outer.read_bytes())
        at = whole.find(inner.read_bytes())
        whole[at + 30] ^= 0xFF  # breaks the outer archive's CRC for inner.zip
        outer.write_bytes(bytes(whole))

        with pytest.raises(MailrunError) as caught:
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)
        assert not isinstance(caught.value, type(None))

    def test_a_truncated_tar_does_not_read_as_holding_nothing(self, tmp_path):
        """A half-finished download used to pass with setup.exe plainly inside.

        `tarfile.ReadError` does not distinguish "not a tar" from "a tar that
        stops early" -- both raise it -- so the catch meant for the first
        silently covered the second.
        """
        blocked = write_file(tmp_path, "setup.exe", b"MZ")
        filler = write_file(tmp_path, "big.bin", b"C" * 200_000)
        full = tmp_path / "full.tar"
        with tarfile.open(full, "w") as archive:
            archive.add(blocked, "setup.exe")
            archive.add(filler, "big.bin")

        half = tmp_path / "half.tar"
        half.write_bytes(full.read_bytes()[:100_000])
        assert b"setup.exe" in half.read_bytes(), "the name is right there in the file"

        with pytest.raises(MailrunError):
            check_attachments([Attachment.from_path(half)], provider=GMAIL)

    def test_a_file_that_is_not_an_archive_at_all_is_still_judged_on_its_suffix(
        self, tmp_path
    ):
        """The case the catch was written for, which must keep working.

        A .zip that is not a zip has nothing to look inside. That is not a
        failure to scan -- there is no archive -- so it passes on its own suffix
        like any other file, rather than being refused as unscannable.
        """
        not_really = write_file(tmp_path, "notes.zip", b"this is plain text")
        check_attachments([Attachment.from_path(not_really)], provider=GMAIL)


class TestTheAttachmentConstructorGuardsItsMimeType:
    """`from_path` always guesses a well-formed type. The class is public.

    Nothing tested the guard, so removing it would ship `Content-Type: pdf/` --
    a malformed header reaching the server through the one package that promises
    to catch such things before the connection opens.
    """

    def test_a_type_without_a_subtype_is_refused(self, tmp_path):
        path = write_file(tmp_path, "report.pdf")
        with pytest.raises(AttachmentError, match="maintype/subtype"):
            Attachment(path=path, mime_type="pdf", size_bytes=path.stat().st_size)

    def test_an_empty_subtype_is_refused(self, tmp_path):
        path = write_file(tmp_path, "report.pdf")
        with pytest.raises(AttachmentError, match="maintype/subtype"):
            Attachment(path=path, mime_type="application/", size_bytes=1)

    def test_the_error_points_at_the_factory_that_gets_it_right(self, tmp_path):
        path = write_file(tmp_path, "report.pdf")
        with pytest.raises(AttachmentError) as caught:
            Attachment(path=path, mime_type="pdf", size_bytes=1)
        assert "from_path" in str(caught.value)

    def test_from_path_produces_one_that_passes(self, tmp_path):
        attachment = Attachment.from_path(write_file(tmp_path, "report.pdf"))
        assert attachment.mime_type == "application/pdf"


class TestANestedArchiveTooBigToLookInside:
    """Decompression is not bounded by the message-size limit.

    A few hundred kilobytes of zip expands to gigabytes, so a nested member is
    read only up to a cap and refused past it. The depth cap had a test; this
    one did not, and a change that read the member anyway would reintroduce the
    bomb while every existing test stayed green.
    """

    def test_a_member_past_the_scan_budget_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mailrun.attachment._MAX_NESTED_ARCHIVE_BYTES", 64)
        inner = write_zip(tmp_path, "inner.zip", ["notes.txt"])
        inner.write_bytes(inner.read_bytes() + b"0" * 128)
        outer = write_zip_of_files(tmp_path, "outer.zip", [inner])

        with pytest.raises(UnscannableArchiveError, match="inner.zip"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)

    def test_a_member_within_it_is_scanned_as_usual(self, tmp_path):
        inner = write_zip(tmp_path, "inner.zip", ["setup.exe"])
        outer = write_zip_of_files(tmp_path, "outer.zip", [inner])
        with pytest.raises(BlockedAttachmentError, match="setup.exe"):
            check_attachments([Attachment.from_path(outer)], provider=GMAIL)
