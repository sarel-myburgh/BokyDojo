"""Upload validation — TODO 0.3.9, SEC 2.3."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.uploads import (
    JPEG,
    PDF,
    PNG,
    UploadRejected,
    generated_storage_name,
    sniff,
    strip_image_metadata,
    validate_upload,
)

PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 200
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 200
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 200
GIF_BYTES = b"GIF89a" + b"0" * 200


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


# -- sniffing -----------------------------------------------------------------


def test_sniff_identifies_known_types():
    assert sniff(PDF_BYTES) is PDF
    assert sniff(PNG_BYTES) is PNG
    assert sniff(JPEG_BYTES) is JPEG


def test_sniff_returns_none_for_unknown():
    assert sniff(b"just some text") is None


def test_webp_magic_is_offset_inside_the_riff_container():
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 100
    assert sniff(webp).label == "WebP"


# -- content wins over filename -----------------------------------------------


def test_valid_pdf_is_accepted():
    assert validate_upload(upload("waiver.pdf", PDF_BYTES)) is PDF


def test_content_type_is_decided_by_bytes_not_by_extension():
    """A .pdf whose bytes are a JPEG is how extension confusion presents."""
    with pytest.raises(UploadRejected, match="named"):
        validate_upload(upload("medical.pdf", JPEG_BYTES))


def test_text_disguised_as_pdf_is_rejected():
    with pytest.raises(UploadRejected, match="Unrecognised"):
        validate_upload(upload("report.pdf", b"<?php system($_GET['c']); ?>"))


def test_extensionless_file_is_judged_purely_on_content():
    assert validate_upload(upload("scan", PNG_BYTES)) is PNG


# -- explicit refusals --------------------------------------------------------


def test_svg_is_rejected_with_a_specific_reason():
    """SVG is a script container that renders as a picture. Never served from
    our own origin."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(UploadRejected, match="scripts"):
        validate_upload(upload("logo.svg", svg))


def test_svg_renamed_to_png_is_still_rejected():
    """Renaming does not help — the bytes are not a PNG either."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(UploadRejected):
        validate_upload(upload("logo.png", svg))


@pytest.mark.parametrize("name", ["x.html", "x.js", "x.exe", "x.zip"])
def test_dangerous_extensions_are_refused(name):
    with pytest.raises(UploadRejected):
        validate_upload(upload(name, PDF_BYTES))


# -- size ---------------------------------------------------------------------


def test_empty_file_is_rejected():
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload(upload("empty.pdf", b""))


def test_oversized_file_is_rejected():
    with pytest.raises(UploadRejected, match="limit"):
        validate_upload(upload("big.pdf", PDF_BYTES), max_bytes=100)


def test_file_at_the_limit_is_accepted():
    assert validate_upload(upload("ok.pdf", PDF_BYTES), max_bytes=len(PDF_BYTES)) is PDF


# -- storage naming -----------------------------------------------------------


def test_storage_name_ignores_the_uploaded_filename():
    """Path traversal, null bytes and extension confusion all die here."""
    name = generated_storage_name("0198f2ab-1111-7000-8000-000000000001", PDF)
    assert "documents/" in name
    assert name.endswith(".pdf")
    assert ".." not in name


def test_storage_names_are_unique_per_document():
    a = generated_storage_name("aaaaaaaa-0000-7000-8000-000000000001", PDF)
    b = generated_storage_name("bbbbbbbb-0000-7000-8000-000000000002", PDF)
    assert a != b


def test_storage_names_shard_by_prefix():
    name = generated_storage_name("ab99f2ab-1111-7000-8000-000000000001", PDF)
    assert name.startswith("documents/ab/")


# -- EXIF stripping -----------------------------------------------------------


def test_exif_including_gps_is_removed_from_photographs():
    """Phone photos carry GPS coordinates. A parent uploading a picture of their
    child must not also be uploading where it was taken."""
    from PIL import Image

    original = io.BytesIO()
    image = Image.new("RGB", (12, 12), (120, 40, 40))
    exif = image.getexif()
    exif[0x010F] = "SecretCameraMake"  # Make
    exif[0x0110] = "SecretCameraModel"  # Model
    exif[0x9C9B] = "child-name-in-metadata"  # XPTitle
    image.save(original, format="JPEG", exif=exif)

    raw = original.getvalue()
    assert b"SecretCameraMake" in raw, "precondition: metadata is present before cleaning"

    cleaned = strip_image_metadata(SimpleUploadedFile("photo.jpg", raw), JPEG)
    cleaned_bytes = cleaned.getvalue()

    assert b"SecretCameraMake" not in cleaned_bytes
    assert b"SecretCameraModel" not in cleaned_bytes

    with Image.open(io.BytesIO(cleaned_bytes)) as reopened:
        # The whole EXIF block is gone, which is what removes GPS coordinates
        # along with everything else — no allow-list of tags to keep in sync.
        assert dict(reopened.getexif()) == {}
        assert reopened.size == (12, 12)


def test_stripping_returns_none_for_non_images():
    assert strip_image_metadata(upload("waiver.pdf", PDF_BYTES), PDF) is None


def test_corrupt_image_is_rejected_rather_than_stored():
    truncated = PNG_BYTES[:12]
    with pytest.raises(UploadRejected):
        strip_image_metadata(upload("broken.png", truncated), PNG)
