"""Fast timings for the device tests."""

from ankerprime.device.a2345 import A2345Timings
from ankerprime.device.a2687 import A2687Timings, ScreenPictureTimings


A2345_FAST = A2345Timings(realtime_refresh=0.05, stale_after=0.15,
                          telemetry_deadline=0.4, settings_retry=0.1,
                          readback_delay=0.02, retry_initial=0.02,
                          retry_max=0.05, tick=0.01,
                          write_waits=(0.05, 0.1), priority_waits=(0.1,))
A2687_FAST = A2687Timings(poll_interval=0.05, stale_after=0.15,
                          telemetry_deadline=0.3, opening_spacing=0.0,
                          reply_timeout=0.2, retry_max=0.05,
                          update_interval=0.2, linger=0.05, energy_save=10.0,
                          tick=0.01)
SCREEN_PICTURE_FAST = ScreenPictureTimings(
    settle_after_select=0.0, settle_after_start=0.0, chunk_spacing=0.0,
    acknowledgement_timeout=0.2, settle_before_verify=0.0,
    verify_attempts=2, verify_delay=0.01)
