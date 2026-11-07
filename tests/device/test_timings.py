"""``replace`` on the device timings."""

import pytest

from ankerprime.device.a2345 import A2345Timings
from ankerprime.device.a2687 import A2687Timings, ScreenPictureTimings


def test_replace_changes_only_the_given_timings() -> None:
    base = A2345Timings(tick=0.5, write_waits=(1.0,))
    changed = base.replace(picture_download=2.0)
    assert changed.picture_download == 2.0
    assert changed.tick == 0.5 and changed.write_waits == (1.0,)
    assert base.picture_download == 45.0


def test_replace_keeps_nested_timings() -> None:
    screen = ScreenPictureTimings(verify_attempts=1)
    changed = A2687Timings(screen_picture=screen).replace(tick=0.1)
    assert changed.tick == 0.1
    assert changed.screen_picture.verify_attempts == 1
    assert screen.replace(budget=1.0).verify_attempts == 1


def test_replace_rejects_unknown_names() -> None:
    with pytest.raises(TypeError):
        A2345Timings().replace(unknown=1.0)
