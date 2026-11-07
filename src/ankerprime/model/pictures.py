"""Pictures: the 250W's clock pictures and the 160W's screen picture."""

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


@dataclass(init=False)
class ScreenPictureUpload:
    """Result of putting a picture on the 160W's screen.

    ``witnessed`` is True when the screen showed another picture before and
    this one after; False when nothing could tell the two apart (the same
    picture was shown already, or the previous one could not be read).
    """

    _picture_id: int
    _crc32: int
    _byte_count: int
    _chunk_count: int
    _previous: int | None
    _witnessed: bool

    def __init__(self, picture_id: int, crc32: int, *, byte_count: int,
                 chunk_count: int, previous: int | None, witnessed: bool):
        self._picture_id = picture_id
        self._crc32 = crc32
        self._byte_count = byte_count
        self._chunk_count = chunk_count
        self._previous = previous
        self._witnessed = witnessed

    @property
    def picture_id(self) -> int:
        return self._picture_id

    @property
    def crc32(self) -> int:
        """Needed with the id to show this picture again."""

        return self._crc32

    @property
    def byte_count(self) -> int:
        return self._byte_count

    @property
    def chunk_count(self) -> int:
        return self._chunk_count

    @property
    def previous(self) -> int | None:
        """Low 16 bits of the picture shown before, when it could be read."""

        return self._previous

    @property
    def witnessed(self) -> bool:
        return self._witnessed
