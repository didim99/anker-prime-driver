"""Picture preparation for the chargers' screens."""

import io
import sys

import pytest
from PIL import Image

from ankerprime.model import InvalidParameterError
from ankerprime.pictures import (
    CLOCK_PICTURE, PictureFormat, PicturesUnavailableError, prepare_picture)


def image_file(width: int, height: int, *, noisy: bool = True) -> bytes:
    """A test image; noise keeps JPEG sizes realistic."""

    if noisy:
        image = Image.merge('RGB', [Image.effect_noise((width, height), 60)
                                    for _ in range(3)])
    else:
        image = Image.new('RGB', (width, height), (200, 30, 30))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def decoded(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize('width, height', [(1200, 300), (300, 1200),
                                           (480, 200), (64, 64)])
def test_clock_picture_fills_the_target(width: int, height: int) -> None:
    jpeg = prepare_picture(image_file(width, height))
    image = decoded(jpeg)
    assert image.format == 'JPEG'
    assert image.size == (480, 200)
    assert len(jpeg) <= CLOCK_PICTURE.max_bytes


def test_quality_lands_in_the_window() -> None:
    jpeg = prepare_picture(image_file(960, 400))
    assert CLOCK_PICTURE.min_bytes <= len(jpeg) <= CLOCK_PICTURE.max_bytes


def test_plain_picture_keeps_the_best_quality_below_the_maximum() -> None:
    jpeg = prepare_picture(image_file(480, 200, noisy=False))
    assert len(jpeg) < CLOCK_PICTURE.min_bytes
    assert decoded(jpeg).size == (480, 200)


def test_too_large_at_every_quality_falls_back_to_the_lowest() -> None:
    tiny = PictureFormat(480, 200, min_bytes=10, max_bytes=20)
    jpeg = prepare_picture(image_file(480, 200), tiny)
    assert decoded(jpeg).size == (480, 200)


def test_letterbox_is_not_needed_for_a_fill_crop() -> None:
    jpeg = prepare_picture(image_file(300, 1200, noisy=False))
    pixel = decoded(jpeg).convert('RGB').getpixel((2, 100))
    assert isinstance(pixel, tuple) and pixel[0] > 150


def test_unreadable_data() -> None:
    with pytest.raises(InvalidParameterError):
        prepare_picture(b'not an image')


def test_without_pillow(monkeypatch: pytest.MonkeyPatch) -> None:
    data = image_file(10, 10)
    monkeypatch.setitem(sys.modules, 'PIL', None)
    with pytest.raises(PicturesUnavailableError, match='pictures'):
        prepare_picture(data)
