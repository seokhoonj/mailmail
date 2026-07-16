"""Attachments, and the rules that decide whether a provider will carry them.

Mail providers reject executable file types outright -- including ones hidden
inside an archive -- and they reject archives they cannot scan. A rejected
attachment fails at the server, which means a bounce arriving minutes later (or
silently landing in a folder nobody reads) rather than an error at the call site.
So the checks here run *before* the connection is opened, and raise.

The other half of the job is the MIME type. Guessing it wrong is the usual reason
an attachment arrives corrupted or inlined into the body instead of attached.
"""

import mimetypes
import tarfile
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from mailrun.errors import (
    AttachmentError,
    BlockedAttachmentError,
    EncryptedArchiveError,
    MessageTooLargeError,
)
from mailrun.provider import MailProvider

__all__ = ["Attachment", "check_attachments", "check_message_size"]

DEFAULT_MIME_TYPE = "application/octet-stream"

# Suffix sets used to decide *how* to look inside an archive. Deliberately keyed
# on the suffix rather than on file contents: an .xlsx or .docx is physically a
# zip, and providers do not treat those as archives to scan.
_ZIP_SUFFIXES = frozenset({".zip"})
_TAR_SUFFIXES = frozenset({".tar", ".tgz", ".tbz", ".tbz2", ".txz"})
_COMPRESSED_SUFFIXES = frozenset({".gz", ".bz2", ".xz"})

# Archive formats with no stdlib reader. Their contents cannot be inspected, so a
# blocked file inside one reaches the server unchecked -- stated in the README
# rather than silently pretended away.
_UNINSPECTABLE_SUFFIXES = frozenset({".7z", ".rar"})

_ZIP_ENCRYPTED_FLAG = 0x1


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

    @classmethod
    def from_path(cls, path: Path | str) -> "Attachment":
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
        f"most {_as_mib(limit_bytes)}; attachments grow about a third when "
        f"encoded, so the raw files must total roughly "
        f"{_as_mib(limit_bytes * 3 // 4)} or less"
    )


def _check_one_attachment(attachment: Attachment, *, provider: MailProvider) -> None:
    blocked = _blocked_names_in(attachment.path, provider.blocked_extensions)
    if blocked:
        raise BlockedAttachmentError(
            f"{provider.name} blocks {_describe_blocked(attachment, blocked)}; "
            f"compressing it does not help -- the block applies inside archives "
            f"too. Share it through a file link instead."
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
        for name in _archive_member_names(path)
        if _is_blocked_name(name, blocked_extensions)
    )
    return blocked


def _is_blocked_name(name: str, blocked_extensions: frozenset[str]) -> bool:
    """Whether a filename's suffix is blocked.

    A `.gz`/`.bz2`/`.xz` wrapper is transparent to the provider: `setup.exe.gz`
    is blocked on the strength of the `.exe` underneath it.
    """
    suffix = Path(name).suffix.lower()
    if suffix in _COMPRESSED_SUFFIXES:
        return _is_blocked_name(Path(name).stem, blocked_extensions)
    return suffix in blocked_extensions


def _archive_member_names(path: Path) -> Iterable[str]:
    """Names stored inside `path`, or nothing when it is not a readable archive.

    Raises
    ------
    EncryptedArchiveError
        The zip is password-protected. Providers reject those whatever is inside,
        because they cannot scan the contents.
    """
    suffix = path.suffix.lower()
    if suffix in _ZIP_SUFFIXES:
        return _zip_member_names(path)
    if suffix in _TAR_SUFFIXES or _is_compressed_tar(path):
        return _tar_member_names(path)
    # A bare .gz/.bz2/.xz wraps a single stream and stores no member list; the
    # name underneath is the path's own stem, which _is_blocked_name already
    # unwraps. Nothing further to look inside.
    return ()


def _is_compressed_tar(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    return len(suffixes) >= 2 and suffixes[-2] == ".tar"


def _zip_member_names(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if any(member.flag_bits & _ZIP_ENCRYPTED_FLAG for member in members):
                raise EncryptedArchiveError(
                    f"{path.name} is password-protected; mail providers reject "
                    f"encrypted archives because they cannot scan the contents"
                )
            return [member.filename for member in members]
    except zipfile.BadZipFile:
        # Not actually a zip despite the suffix. Nothing to look inside; the
        # provider will judge it on its own suffix like any other file.
        return []


def _tar_member_names(path: Path) -> list[str]:
    try:
        with tarfile.open(path) as archive:
            return archive.getnames()
    except tarfile.TarError:
        return []


def _as_mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MiB"
