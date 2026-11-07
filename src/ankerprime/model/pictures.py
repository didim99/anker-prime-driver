"""Clock pictures of the 250W: the cloud catalogue and the user's own."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ankerprime.model.enums import ClockTheme


@dataclass(init=False)
class ClockPicture:
    """One picture the 250W can show behind its clock.

    The charger downloads it itself over Wi-Fi from ``image_url`` and checks
    it against ``crc32``. Links of own pictures are signed for a few minutes
    only, so they are listed again right before use.
    """

    _picture_id: int
    _title: str
    _category: str
    _image_url: str
    _crc32: int
    _text_color: int | None
    _own: bool

    OWN_LIMIT: ClassVar[int] = 8
    """The account keeps at most this many own pictures."""

    def __init__(self, picture_id: int, image_url: str, crc32: int, *,
                 title: str = '', category: str = '',
                 text_color: int | None = None, own: bool = False):
        self._picture_id = picture_id
        self._title = title
        self._category = category
        self._image_url = image_url
        self._crc32 = crc32
        self._text_color = text_color
        self._own = own

    @property
    def picture_id(self) -> int:
        return self._picture_id

    @property
    def title(self) -> str:
        return self._title

    @property
    def category(self) -> str:
        """Catalogue category; empty for own pictures."""

        return self._category

    @property
    def image_url(self) -> str:
        return self._image_url

    @property
    def crc32(self) -> int:
        return self._crc32

    @property
    def text_color(self) -> int | None:
        """ARGB colour of the clock digits over this picture, if set."""

        return self._text_color

    @property
    def own(self) -> bool:
        """Uploaded by the user rather than taken from the catalogue."""

        return self._own

    @property
    def theme(self) -> ClockTheme:
        return ClockTheme.CUSTOM if self._own else ClockTheme.PICTURE
