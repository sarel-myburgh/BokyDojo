"""Upload validation — TODO 0.3.9 / SEC 2.3.

What gets uploaded here: medical letters, signed waivers, birth certificates and
passports for tournament age verification, and photographs of children. Per
SEC §1.1 that is the most damaging content in the system, and per SEC §1.3 file
upload is one of the seven highest-risk surfaces.

Three separate defences, because each covers a different attack:

  1. **Magic-byte sniffing.** A filename and a Content-Type header are both
     attacker-controlled. The first bytes of the file are what actually decide
     how a browser or image library will treat it.
  2. **SVG is rejected outright.** SVG is a script container that happens to
     render as a picture. There is no safe way to serve attacker-supplied SVG
     from our own origin, and no dojo needs one.
  3. **Images are re-encoded, not merely accepted.** This strips EXIF —
     including the GPS coordinates embedded in most phone photos. A parent
     uploading a picture of their child should not also be uploading the
     location it was taken.

Storage naming is generated, never derived from the uploaded filename: that
removes path traversal and extension-confusion in one step. The original name is
kept as a separate display-only field.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000  # guards against decompression-bomb images


@dataclass(frozen=True)
class FileKind:
    label: str
    extensions: tuple[str, ...]
    mime: str
    magic: tuple[bytes, ...]
    is_image: bool = False
    #: Offset at which the magic bytes appear (RIFF containers need this).
    offset: int = 0


PDF = FileKind("PDF", (".pdf",), "application/pdf", (b"%PDF-",))
JPEG = FileKind("JPEG", (".jpg", ".jpeg"), "image/jpeg", (b"\xff\xd8\xff",), is_image=True)
PNG = FileKind(
    "PNG", (".png",), "image/png", (b"\x89PNG\r\n\x1a\n",), is_image=True
)
GIF = FileKind("GIF", (".gif",), "image/gif", (b"GIF87a", b"GIF89a"), is_image=True)
WEBP = FileKind("WebP", (".webp",), "image/webp", (b"WEBP",), is_image=True, offset=8)

ALLOWED_KINDS: tuple[FileKind, ...] = (PDF, JPEG, PNG, GIF, WEBP)

#: Extensions refused with a specific explanation rather than a generic one, so
#: the person uploading understands why and what to do instead.
EXPLICITLY_REFUSED = {
    ".svg": _("SVG files can contain scripts and are not accepted. Export as PNG or JPEG."),
    ".svgz": _("SVG files can contain scripts and are not accepted. Export as PNG or JPEG."),
    ".htm": _("HTML files are not accepted."),
    ".html": _("HTML files are not accepted."),
    ".exe": _("Executable files are not accepted."),
    ".js": _("Script files are not accepted."),
    ".zip": _("Archives are not accepted. Upload the documents individually."),
}


class UploadRejected(ValidationError):
    """An upload failed validation."""


def sniff(header: bytes) -> FileKind | None:
    """Identify a file from its leading bytes. Returns None if unrecognised."""
    for kind in ALLOWED_KINDS:
        for magic in kind.magic:
            if header[kind.offset : kind.offset + len(magic)] == magic:
                return kind
    return None


def _extension_of(filename: str) -> str:
    name = filename or ""
    # rpartition returns the whole string as the tail when there is no
    # separator, so an extensionless "scan" would otherwise become ".scan".
    if "." not in name:
        return ""
    return f".{name.rpartition('.')[2].lower()}"


def validate_upload(uploaded_file, *, max_bytes: int = MAX_UPLOAD_BYTES) -> FileKind:
    """Validate an uploaded file and return the kind its *content* says it is.

    Raises ``UploadRejected``. Never trusts the filename or the declared
    content type — both come from the client.
    """
    size = getattr(uploaded_file, "size", None)
    if size is None:
        uploaded_file.seek(0, io.SEEK_END)
        size = uploaded_file.tell()

    if size == 0:
        raise UploadRejected(_("The file is empty."))
    if size > max_bytes:
        raise UploadRejected(
            _("The file is %(size)s MB; the limit is %(limit)s MB.")
            % {"size": round(size / 1024 / 1024, 1), "limit": round(max_bytes / 1024 / 1024)}
        )

    extension = _extension_of(getattr(uploaded_file, "name", ""))
    if extension in EXPLICITLY_REFUSED:
        raise UploadRejected(EXPLICITLY_REFUSED[extension])

    uploaded_file.seek(0)
    header = uploaded_file.read(32)
    uploaded_file.seek(0)

    kind = sniff(header)
    if kind is None:
        raise UploadRejected(
            _("Unrecognised file type. Accepted: PDF, JPEG, PNG, GIF, WebP.")
        )

    # A .pdf whose bytes are a JPEG is not necessarily an attack, but it is
    # always a mistake worth surfacing — and it is how extension-confusion
    # attacks present.
    if extension and extension not in kind.extensions:
        raise UploadRejected(
            _("The file is a %(actual)s but is named %(ext)s. Rename it to match.")
            % {"actual": kind.label, "ext": extension}
        )

    return kind


def strip_image_metadata(uploaded_file, kind: FileKind):
    """Re-encode an image, discarding EXIF and any embedded payload.

    Returns a ``BytesIO`` of the cleaned image, or None if the file is not an
    image. Requires Pillow.
    """
    if not kind.is_image:
        return None

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    uploaded_file.seek(0)
    try:
        with Image.open(uploaded_file) as image:
            image.load()
            # Rebuilding the image from raw pixel bytes is what actually drops
            # the metadata — saving the original object would carry EXIF
            # through. Palette and CMYK modes are normalised first because they
            # cannot round-trip through tobytes() without their palette.
            source = image.convert("RGB") if image.mode in ("P", "PA", "CMYK") else image
            cleaned = Image.frombytes(source.mode, source.size, source.tobytes())

            buffer = io.BytesIO()
            fmt = "JPEG" if kind is JPEG else kind.label.upper()
            if fmt == "WEBP":
                fmt = "WEBP"
            cleaned.save(buffer, format=fmt)
            buffer.seek(0)
            return buffer
    except Exception as exc:  # Pillow raises a wide variety here
        raise UploadRejected(
            _("The image could not be processed and was not accepted.")
        ) from exc
    finally:
        uploaded_file.seek(0)


def generated_storage_name(document_id, kind: FileKind) -> str:
    """Storage path derived from our own id, never from the uploaded name.

    Removes path traversal, null-byte tricks and extension confusion in one
    step, and means two people uploading `passport.pdf` cannot collide.
    """
    identifier = str(document_id)
    # Shard so no single directory accumulates every file in the deployment.
    return f"documents/{identifier[:2]}/{identifier}{kind.extensions[0]}"
