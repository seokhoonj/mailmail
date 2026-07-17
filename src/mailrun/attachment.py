"""Attachments, and the rules that decide whether a provider will carry them.

Mail providers reject executable file types outright -- including ones hidden
inside an archive -- and they reject archives they cannot scan. A rejected
attachment fails at the server, which means a bounce arriving minutes later (or
silently landing in a folder nobody reads) rather than an error at the call site.
So the checks here run *before* the connection is opened, and raise.

The other half of the job is the MIME type. Guessing it wrong is the usual reason
an attachment arrives corrupted or inlined into the body instead of attached.
"""

import io
import mimetypes
import tarfile
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Self

from mailrun.errors import (
    AttachmentError,
    BlockedAttachmentError,
    EncryptedArchiveError,
    MessageTooLargeError,
    UnscannableArchiveError,
)
from mailrun.provider import MailProvider

__all__ = [
    "Attachment",
    "check_attachments",
    "check_message_size",
    "estimated_encoded_bytes",
]

DEFAULT_MIME_TYPE = "application/octet-stream"

# Suffix sets used to decide *how* to look inside an archive. Deliberately keyed
# on the suffix rather than on file contents: an .xlsx or .docx is physically a
# zip, and providers do not treat those as archives to scan.
_ZIP_SUFFIXES = frozenset({".zip"})
_TAR_SUFFIXES = frozenset({".tar", ".tgz", ".tbz", ".tbz2", ".txz"})
_COMPRESSED_SUFFIXES = frozenset({".gz", ".bz2", ".xz"})

_ZIP_ENCRYPTED_FLAG = 0x1

# How much an attachment grows on the way out. base64 is 4/3 of the raw bytes,
# and the encoder breaks the line every 76 characters, so each of those lines
# also carries a CRLF. Measured against a real 25 MiB attachment: 1.3684.
#
# The obvious 4/3 forgets the line breaks, and the difference is not academic --
# it told a caller their files could total 25.7 MiB against Gmail when the true
# ceiling is 25.0, so anyone who trusted the number was handed a bounce.
_ENCODED_EXPANSION = (4 / 3) * (78 / 76)

# How far to descend through archives-inside-archives. Deep enough for anything
# an honest sender produces; shallow enough that a nest built to exhaust the
# scanner stops instead. Hitting it means "we could not scan to the bottom",
# which is refused rather than waved through.
_MAX_ARCHIVE_DEPTH = 4

# Most a nested archive is read into memory to inspect it. The outer file is
# already capped by the message-size limit, but decompression is not -- a few
# hundred kilobytes of zip can expand to gigabytes. A member larger than this is
# refused as unscannable rather than expanded.
_MAX_NESTED_ARCHIVE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Attachment:
    """One file to attach, with the MIME type it will be sent as.

    Attributes
    ----------
    path
        Location of the file on disk.
    mime_type
        Full type, e.g. `application/pdf`. Guessed from the suffix.
    size_bytes
        Size on disk, before base64 encoding inflates it by roughly a third.
    """

    path: Path
    mime_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        # `from_path` always produces a well-formed type, but the class is public
        # and freely constructible: `mime_type="pdf"` used to go out as
        # `Content-Type: pdf/` -- a malformed header reaching the server through
        # the one package that promises to catch such things first.
        maintype, slash, subtype = self.mime_type.partition("/")
        if not (maintype and slash and subtype):
            raise AttachmentError(
                f"mime_type must read 'maintype/subtype', not "
                f"{self.mime_type!r}; Attachment.from_path guesses a valid one "
                f"from the suffix"
            )

    @classmethod
    def from_path(cls, path: Path | str) -> Self:
        """Read a file's metadata and guess its MIME type.

        Raises
        ------
        AttachmentError
            If the path does not exist or is not a regular file.
        """
        path = Path(path).expanduser()
        if not path.exists():
            raise AttachmentError(f"attachment not found: {path}")
        if not path.is_file():
            raise AttachmentError(f"attachment is not a regular file: {path}")
        guessed, _encoding = mimetypes.guess_type(path.name)
        return cls(
            path       = path,
            mime_type  = guessed or DEFAULT_MIME_TYPE,
            size_bytes = path.stat().st_size,
        )

    @property
    def filename(self) -> str:
        """The name the recipient will see."""
        return self.path.name


def check_attachments(
    attachments: Sequence[Attachment], *, provider: MailProvider
) -> None:
    """Raise if `provider` would reject any attachment.

    Checks each file's own suffix, then the names inside it when it is an archive
    the standard library can read.

    Raises
    ------
    BlockedAttachmentError
        A file, or a member of an archive, has a suffix the provider blocks.
    EncryptedArchiveError
        A zip is password-protected, which providers reject unopened.
    """
    for attachment in attachments:
        _check_one_attachment(attachment, provider=provider)


def estimated_encoded_bytes(attachments: Iterable[Attachment]) -> int:
    """A floor on what these attachments will weigh once encoded, from `stat`.

    Cheap on purpose, and asked before the message is assembled, because
    assembling it is the expensive part: `to_mime` reads every attachment into
    memory and flattening lays a base64 copy beside it. Weighing only the
    finished article meant a 200 MiB attachment cost about 1.5 GB of resident
    memory to earn the answer "too large" -- an answer `size_bytes` already
    implied, stat'd at construction and, until this function, never read by
    anything. On a large enough attachment the OOM killer arrives first: SIGKILL,
    no traceback, no `MessageTooLargeError` for a caller to catch, which is the
    silent failure this package exists to turn into a raise.

    Deliberately an underestimate, so refusing on it can never refuse a message
    the exact check would have passed: it counts base64 of the attachments alone
    at a ratio a hair under the true one, and leaves out headers, body, and MIME
    boundaries, all of which only add. Measured at 25 MiB, it lands ~600 bytes
    below the real wire size. Anything it lets through is still weighed for real.
    """
    raw_bytes = sum(attachment.size_bytes for attachment in attachments)
    return int(raw_bytes * _ENCODED_EXPANSION)


def check_message_size(encoded_bytes: int, *, limit_bytes: int) -> None:
    """Raise if the encoded message exceeds the server's limit.

    Parameters
    ----------
    encoded_bytes
        Size of the fully encoded MIME message -- what the server actually
        weighs, not the sum of the raw files.
    limit_bytes
        Largest message the server accepts.

    Raises
    ------
    MessageTooLargeError
    """
    if encoded_bytes <= limit_bytes:
        return
    raise MessageTooLargeError(
        f"encoded message is {_as_mib(encoded_bytes)} but the server accepts at "
        f"most {_as_mib(limit_bytes)}; attachments grow by about 37% on the way "
        f"out, so the raw files must total roughly "
        f"{_as_mib(int(limit_bytes / _ENCODED_EXPANSION))} or less"
    )


def _check_one_attachment(attachment: Attachment, *, provider: MailProvider) -> None:
    blocked = _blocked_names_in(attachment.path, provider.blocked_extensions)
    if blocked:
        raise BlockedAttachmentError(
            f"{provider.name} blocks {_describe_blocked(attachment, blocked)}; "
            f"putting it in a .zip or .tar.gz does not help, because those are "
            f"looked inside. Share it through a file link instead."
        )


def _describe_blocked(attachment: Attachment, blocked: Sequence[str]) -> str:
    if blocked == [attachment.filename]:
        return f"{attachment.filename} ({attachment.path.suffix})"
    inside = ", ".join(blocked)
    return f"{attachment.filename}, which contains: {inside}"


def _blocked_names_in(path: Path, blocked_extensions: frozenset[str]) -> list[str]:
    """Names at or inside `path` whose suffix the provider blocks."""
    blocked = []
    if _is_blocked_name(path.name, blocked_extensions):
        blocked.append(path.name)
    blocked.extend(
        name
        for name in _archive_member_names(path, path.name)
        if _is_blocked_name(name, blocked_extensions)
    )
    return blocked


def _is_blocked_name(name: str, blocked_extensions: frozenset[str]) -> bool:
    """Whether a filename's suffix is blocked.

    Trailing dots and spaces come off first. Windows drops them when it writes
    the file, so `setup.exe.` lands on the recipient's disk as `setup.exe` and
    runs -- and Gmail refuses that name, verified against the live server, so
    reading the suffix as typed would pass a message the provider bounces.

    A `.gz`/`.bz2`/`.xz` wrapper is then peeled: `setup.exe.gz` is blocked on the
    strength of the `.exe` underneath it. Peeling is a loop and not recursion
    because these names are not bounded the way real filenames are -- the
    filesystem caps a path at 255 bytes, but a zip may store a 64 KB member name,
    and a thousand stacked `.gz` was enough to exhaust the stack.
    """
    stem = _without_trailing_padding(Path(name).name)
    while (suffix := Path(stem).suffix.lower()) in _COMPRESSED_SUFFIXES:
        stem = _without_trailing_padding(Path(stem).stem)
    return suffix in blocked_extensions


def _without_trailing_padding(name: str) -> str:
    """A filename without the trailing dots and spaces Windows discards."""
    return name.rstrip(". ")


def _archive_member_names(
    source: Path | IO[bytes], name: str, *, depth: int = 0
) -> Iterator[str]:
    """Every name stored inside `source`, descending through nested archives.

    Yields nothing when `name` is not an archive this can read. Descends because
    one level is not what a provider does: `outer.zip` holding `inner.zip`
    holding `setup.exe` is refused by Gmail, so stopping at `inner.zip` would
    pass a message that bounces.

    Parameters
    ----------
    source
        The archive, as a path or an open byte stream (a nested member).
    name
        The archive's filename, which is what decides how to read it. Passed
        separately because a nested member has no path of its own.
    depth
        How many archives have already been opened to reach this one.

    Raises
    ------
    EncryptedArchiveError
        A zip is password-protected.
    UnscannableArchiveError
        The nest is too deep, or a nested member too large, to scan to the
        bottom. Providers reject what they cannot look inside, and so does this.
    """
    suffix = Path(name).suffix.lower()
    if suffix in _ZIP_SUFFIXES:
        yield from _zip_member_names(source, name, depth=depth)
    elif suffix in _TAR_SUFFIXES or _is_compressed_tar(name):
        yield from _tar_member_names(source, name, depth=depth)
    # A bare .gz/.bz2/.xz wraps a single stream and stores no member list; the
    # name underneath is the path's own stem, which _is_blocked_name already
    # unwraps. Nothing further to look inside.


def _is_compressed_tar(name: str) -> bool:
    suffixes = [suffix.lower() for suffix in Path(name).suffixes]
    return len(suffixes) >= 2 and suffixes[-2] == ".tar"


def _zip_member_names(
    source: Path | IO[bytes], name: str, *, depth: int = 0
) -> Iterator[str]:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile:
        # Not actually a zip despite the suffix. Nothing to look inside; the
        # provider will judge it on its own suffix like any other file.
        return
    # Only the open is allowed to mean "not an archive". This used to wrap the
    # loop as well, and a `BadZipFile` raised while *reading* a member -- a bad
    # CRC from one flipped byte -- was caught by the same clause and returned,
    # ending the walk. Every member after the corrupt one went unlisted, and the
    # caller was told the archive held nothing blocked. One byte was enough to
    # carry setup.exe through the gate.
    with archive:
        members = archive.infolist()
        if any(member.flag_bits & _ZIP_ENCRYPTED_FLAG for member in members):
            raise EncryptedArchiveError(
                f"{name} is password-protected; mail providers reject "
                f"encrypted archives because they cannot scan the contents"
            )
        for member in members:
            yield member.filename
            if not _is_archive_name(member.filename):
                continue
            try:
                with archive.open(member) as nested:
                    yield from _nested_member_names(
                        nested, member.filename, outer=name, depth=depth
                    )
            except (zipfile.BadZipFile, EOFError) as err:
                raise UnscannableArchiveError(
                    f"{member.filename} inside {name} cannot be read to the end "
                    f"({err}); what cannot be scanned must not pass as what "
                    f"holds nothing"
                ) from err


def _tar_member_names(
    source: Path | IO[bytes], name: str, *, depth: int = 0
) -> Iterator[str]:
    try:
        # Opened inside its own try because tarfile.open itself is what raises on
        # a file that is not a tar -- the `with` cannot start until it returns.
        opened = _open_tar(source)
    except tarfile.ReadError as err:
        if _looks_like_a_tar(source):
            raise UnscannableArchiveError(
                f"{name} carries a tar header but will not open ({err}); an "
                f"archive that cannot be read must not pass as one that holds "
                f"nothing"
            ) from err
        # Nothing here reads as a tar at all. Nothing to look inside; the
        # provider will judge it on its own suffix like any other file.
        return
    with opened as archive:
        # Reading the members is a separate risk from opening, and used to share
        # the clause above. The comment there claimed the narrow `ReadError` told
        # "not a tar" apart from "a broken tar" -- tarfile draws no such line,
        # raising ReadError for both, so the catch written for the first silently
        # covered the second. A half-finished download of a tar holding setup.exe
        # came back as "nothing blocked", with the name plainly in the bytes.
        try:
            members = archive.getmembers()
        except (tarfile.ReadError, EOFError) as err:
            raise UnscannableArchiveError(
                f"{name} stops before its last member ({err}); an archive that "
                f"cannot be read to the end must not pass as one that holds "
                f"nothing"
            ) from err
        for member in members:
            yield member.name
            if not (member.isfile() and _is_archive_name(member.name)):
                continue
            nested = archive.extractfile(member)
            if nested is None:
                continue
            try:
                yield from _nested_member_names(
                    nested, member.name, outer=name, depth=depth
                )
            except (tarfile.ReadError, EOFError) as err:
                raise UnscannableArchiveError(
                    f"{member.name} inside {name} cannot be read to the end "
                    f"({err}); what cannot be scanned must not pass as what "
                    f"holds nothing"
                ) from err


def _open_tar(source: Path | IO[bytes]) -> tarfile.TarFile:
    """A tar from a path or an already-open stream (a nested member)."""
    if isinstance(source, Path):
        return tarfile.open(source)
    return tarfile.open(fileobj=source)


# Offset and value of the format stamp POSIX puts in every tar header.
_TAR_MAGIC_AT, _TAR_MAGIC = 257, b"ustar"


def _looks_like_a_tar(source: Path | IO[bytes]) -> bool:
    """Whether the first block carries a tar header, whatever `tarfile` said.

    Asked only when `tarfile.open` has already refused, to tell its two answers
    apart. It raises the same `ReadError` for "this was never a tar" and for "a
    tar whose first header stops mid-field", and under `r:*` even flattens the
    second into the first's wording, because it tries every compression before
    giving up. The distinction matters: the first is an ordinary file to be
    judged on its suffix; the second could hold anything, and reported nothing.

    Only plain tars are recognised here, which is all that is needed -- a
    compressed one that stops early fails inside the decompressor, and that is
    `getmembers`' EOFError, not this.
    """
    try:
        if isinstance(source, Path):
            head = source.read_bytes()[: _TAR_MAGIC_AT + len(_TAR_MAGIC)]
        else:
            source.seek(0)
            head = source.read(_TAR_MAGIC_AT + len(_TAR_MAGIC))
            source.seek(0)
    except OSError:
        return False
    return head[_TAR_MAGIC_AT : _TAR_MAGIC_AT + len(_TAR_MAGIC)] == _TAR_MAGIC


def _nested_member_names(
    nested: IO[bytes], name: str, *, outer: str, depth: int
) -> Iterator[str]:
    """Names inside an archive that is itself a member of another archive.

    Reads the member into memory to read it as an archive, which is why both the
    depth and the size are capped here rather than left to the caller.
    """
    if depth + 1 >= _MAX_ARCHIVE_DEPTH:
        raise UnscannableArchiveError(
            f"{outer} nests archives more than {_MAX_ARCHIVE_DEPTH} deep at "
            f"{name}; mail providers reject what they cannot scan to the bottom, "
            f"and neither this nor they will unpack it further"
        )
    payload = nested.read(_MAX_NESTED_ARCHIVE_BYTES + 1)
    if len(payload) > _MAX_NESTED_ARCHIVE_BYTES:
        raise UnscannableArchiveError(
            f"{name} inside {outer} expands past "
            f"{_as_mib(_MAX_NESTED_ARCHIVE_BYTES)}, so it cannot be scanned "
            f"without unpacking more than the message could ever carry"
        )
    yield from _archive_member_names(io.BytesIO(payload), name, depth=depth + 1)


def _is_archive_name(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return (
        suffix in _ZIP_SUFFIXES
        or suffix in _TAR_SUFFIXES
        or _is_compressed_tar(name)
    )


def _as_mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MiB"
