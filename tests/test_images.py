from io import BytesIO

import pytest
from PIL import Image

from app.images import MAX_FILE_BYTES, NORMALIZED_JPEG_QUALITY, ImageInputError, prepare_image
from tests.support import jpeg_bytes


def test_normalizes_valid_image_and_removes_source_metadata(tmp_path) -> None:
    prepared, warnings = prepare_image(BytesIO(jpeg_bytes()), tmp_path, 1)

    assert prepared.path.read_bytes().startswith(b"\xff\xd8\xff")
    assert prepared.original_format == "JPEG"
    assert prepared.width == 800
    assert prepared.height == 800
    assert warnings == []


def test_uses_medium_jpeg_quality_baseline() -> None:
    assert NORMALIZED_JPEG_QUALITY == 75


@pytest.mark.parametrize(
    "source_format", ["JPEG", "PNG", "GIF", "TIFF", "BMP", "WEBP", "HEIF", "AVIF"]
)
def test_normalizes_every_ebay_supported_format(source_format, tmp_path) -> None:
    source = BytesIO()
    Image.new("RGB", (600, 700), "#335f84").save(source, format=source_format)
    source.seek(0)

    prepared, _warnings = prepare_image(source, tmp_path, 1)

    assert prepared.original_format == source_format
    assert prepared.path.read_bytes().startswith(b"\xff\xd8\xff")


def test_warns_for_photo_below_ebay_minimum(tmp_path) -> None:
    _prepared, warnings = prepare_image(BytesIO(jpeg_bytes(320, 400)), tmp_path, 1)

    assert warnings[0].photo_number == 1
    assert "500-pixel" in warnings[0].message


def test_rejects_bytes_over_per_photo_limit_before_decoding(tmp_path) -> None:
    with pytest.raises(ImageInputError) as error:
        prepare_image(BytesIO(b"x" * (MAX_FILE_BYTES + 1)), tmp_path, 1)

    assert error.value.code == "photo_too_large"
    assert error.value.status_code == 413


def test_rejects_non_image_content(tmp_path) -> None:
    with pytest.raises(ImageInputError) as error:
        prepare_image(BytesIO(b"not a photo"), tmp_path, 1)

    assert error.value.code == "invalid_photo"
    assert error.value.status_code == 415
