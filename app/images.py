"""Bounded upload validation and sequential image normalization."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from app.models import ImageWarning

MAX_PHOTOS = 24
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_NOTE_CHARACTERS = 2_000
MAX_NORMALIZED_EDGE = 2_048
NORMALIZED_JPEG_QUALITY = 75
READ_CHUNK_BYTES = 64 * 1024
ALLOWED_SOURCE_FORMATS = frozenset(
    {"JPEG", "MPO", "PNG", "GIF", "TIFF", "BMP", "WEBP", "HEIF", "AVIF"}
)

register_heif_opener()


class ImageInputError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedImage:
    path: Path
    mime_type: str
    original_format: str
    width: int
    height: int


def _copy_bounded(source: BinaryIO, destination: Path) -> int:
    total = 0
    with destination.open("wb") as output:
        while chunk := source.read(READ_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ImageInputError(
                    "photo_too_large",
                    "Each photo must be 12 MB or smaller.",
                    413,
                )
            output.write(chunk)

    if total == 0:
        raise ImageInputError("empty_photo", "One of the selected photos is empty.", 400)
    return total


def _flatten_transparency(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def prepare_image(
    source: BinaryIO,
    workdir: Path,
    photo_number: int,
) -> tuple[PreparedImage, list[ImageWarning]]:
    source_path = workdir / f"source-{photo_number}"
    output_path = workdir / f"normalized-{photo_number}.jpg"
    _copy_bounded(source, source_path)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source_path) as opened:
                source_format = (opened.format or "").upper()
                if source_format not in ALLOWED_SOURCE_FORMATS:
                    raise ImageInputError(
                        "unsupported_photo",
                        "Use a JPEG, PNG, GIF, TIFF, BMP, WEBP, HEIC, or AVIF photo.",
                        415,
                    )

                opened.seek(0)
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                width, height = oriented.size
                normalized = _flatten_transparency(oriented)
                normalized.thumbnail(
                    (MAX_NORMALIZED_EDGE, MAX_NORMALIZED_EDGE),
                    Image.Resampling.LANCZOS,
                )
                normalized.save(
                    output_path,
                    format="JPEG",
                    quality=NORMALIZED_JPEG_QUALITY,
                    optimize=True,
                )
                normalized.close()
    except ImageInputError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageInputError(
            "photo_dimensions_too_large",
            "One photo has unusually large dimensions. Choose a smaller version.",
            413,
        ) from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageInputError(
            "invalid_photo",
            "One selected file could not be read as a supported photo.",
            415,
        ) from None

    image_warnings: list[ImageWarning] = []
    if max(width, height) < 500:
        image_warnings.append(
            ImageWarning(
                photo_number=photo_number,
                message=(
                    "This photo is below eBay's 500-pixel minimum; use a larger photo if possible."
                ),
            )
        )

    return (
        PreparedImage(
            path=output_path,
            mime_type="image/jpeg",
            original_format=source_format,
            width=width,
            height=height,
        ),
        image_warnings,
    )
