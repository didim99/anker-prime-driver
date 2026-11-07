"""Picture preparation for the chargers' screens (``[pictures]`` extra).

A picture fills the target size (centre crop, black where the source does
not reach) and is saved as a JPEG whose quality is searched to land in the
size window the official app produces. Chroma is kept at full resolution
(4:4:4): with Pillow's default 4:2:0 even detailed photos stay below that
window.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ankerprime.model.errors import AnkerPrimeError, InvalidParameterError


if TYPE_CHECKING:
    from PIL.Image import Image as PilImage


@dataclass(init=False)
class PictureFormat:
    """Target size in pixels and the accepted JPEG size in bytes."""

    _width: int
    _height: int
    _min_bytes: int
    _max_bytes: int

    def __init__(self, width: int, height: int, *, min_bytes: int,
                 max_bytes: int):
        self._width = width
        self._height = height
        self._min_bytes = min_bytes
        self._max_bytes = max_bytes

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def min_bytes(self) -> int:
        """Smallest size the quality search aims for."""

        return self._min_bytes

    @property
    def max_bytes(self) -> int:
        """Largest size accepted."""

        return self._max_bytes


CLOCK_PICTURE = PictureFormat(480, 200, min_bytes=78_643, max_bytes=98_304)
"""Clock picture of the 250W."""

QUALITY_RANGE = (1, 90)
QUALITY_START = 45
QUALITY_STEPS = 8
FULL_CHROMA = 0


class PicturesUnavailableError(AnkerPrimeError):
    """Pillow is missing; install ``anker-prime-driver[pictures]``."""


def prepare_picture(data: bytes, target: PictureFormat = CLOCK_PICTURE
                    ) -> bytes:
    """Fill ``target`` with an image file and encode it as a JPEG.

    Raises ``InvalidParameterError`` for data Pillow cannot read and
    ``PicturesUnavailableError`` without Pillow.
    """

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise PicturesUnavailableError(
            'picture support needs Pillow: install '
            '"anker-prime-driver[pictures]"') from None
    try:
        with Image.open(io.BytesIO(data)) as source:
            oriented = ImageOps.exif_transpose(source)
            image = oriented.convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise InvalidParameterError(f'not a readable image: {error}') from None
    scale = max(target.width / image.width, target.height / image.height)
    size = (max(1, round(image.width * scale)),
            max(1, round(image.height * scale)))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (target.width, target.height), (0, 0, 0))
    canvas.paste(resized, ((target.width - size[0]) // 2,
                           (target.height - size[1]) // 2))
    return _search_quality(canvas, target)


def _search_quality(image: PilImage, target: PictureFormat) -> bytes:
    """Binary search of the JPEG quality for a size inside the window.

    The largest file not above the maximum wins when none reaches the
    minimum; quality 1 when every one is too large.
    """

    low, high = QUALITY_RANGE
    quality = QUALITY_START
    best = None
    for _ in range(QUALITY_STEPS):
        data = _jpeg(image, quality)
        if len(data) > target.max_bytes:
            high = quality - 1
        else:
            best = data
            if len(data) >= target.min_bytes:
                return data
            low = quality + 1
        if low > high:
            break
        quality = (low + high) // 2
    return best if best is not None else _jpeg(image, QUALITY_RANGE[0])


def _jpeg(image: PilImage, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=quality,
               subsampling=FULL_CHROMA)
    return output.getvalue()
