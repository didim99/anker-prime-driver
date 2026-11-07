"""Every A2345 setting as one snapshot reports it, kept up to date."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from ankerprime.model.enums import OutputId
from ankerprime.protocol.a2345.codes import GROUPS, USB_C_GROUPS
from ankerprime.protocol.a2345.fields import FieldReader
from ankerprime.protocol.a2345.frame import Frame
from ankerprime.protocol.a2345.settings import (
    CUSTOM_CHARGING_MODE_CODE, OVER_TEMPERATURE_CODE, PORT_PRIORITY_MODE_CODE,
    PRIORITY_NORMAL, PRIORITY_OFF, PRIORITY_PREFERRED, TAG_BRAND_IDENTITY,
    TAG_BRIGHTNESS, TAG_CAPABILITIES, TAG_CHARGING_MODE, TAG_CLOCK,
    TAG_CLOCK_SCHEDULE, TAG_CUSTOM_PROFILE, TAG_DEVICE_IDENTIFICATION,
    TAG_ERROR_CODE, TAG_FIRMWARE, TAG_FIRST_OUTPUT_CONTROL, TAG_HOUR_FORMAT,
    TAG_KNOB_DIRECTION, TAG_MAX_COMPATIBILITY, TAG_PORT_PROTOCOLS,
    TAG_PROTOCOL_VERSION, TAG_SCREEN_TIMEOUT, TAG_UFCS, TAG_UPDATING_FIRMWARE,
    TAG_USB_IDENTITY, THEME_PICTURE_CODES, Capabilities, ClockState,
    ClockWindow, CustomProfile, IdentitySlot, OutputControl, PortProtocol,
    SettingField, decode_clock_window, decode_custom_profile,
    decode_identities, decode_output_control, decode_port_protocols,
    decode_snapshot_clock, decode_version, switch_value)
from ankerprime.protocol.a2345.updates import (
    ChargingModeUpdate, ClockUpdate, CountdownUpdate,
    FirmwareVersionUpdate, OutputSwitchUpdate, PriorityUpdate,
    ScheduleUpdate, SettingsUpdate, ThemeDownloadUpdate, ValueUpdate)


class DeviceSettings:
    """Everything controllable the charger reports, merged over time.

    None means not reported yet or, for capability-gated fields, not
    supported by the firmware.
    """

    _firmware_version: str | None
    _error_code: int | None
    _outputs: dict[OutputId, OutputControl]
    _clock: ClockState | None
    _image_time_display: bool | None
    _screen_timeout: int | None
    _charging_mode: int | None
    _updating_firmware: bool | None
    _brightness: int | None
    _knob_direction: int | None
    _hour_format: int | None
    _protocol_version: int | None
    _capabilities: Capabilities | None
    _custom_profile: CustomProfile | None
    _clock_schedule: ClockWindow | None
    _port_protocols: tuple[PortProtocol, ...] | None
    _max_compatibility: bool | None
    _ufcs: bool | None
    _device_identification: bool | None
    _usb_identities: tuple[IdentitySlot, ...]
    _brand_identities: tuple[IdentitySlot, ...]

    def __init__(self) -> None:
        self._firmware_version = None
        self._error_code = None
        self._outputs = {}
        self._clock = None
        self._image_time_display = None
        self._screen_timeout = None
        self._charging_mode = None
        self._updating_firmware = None
        self._brightness = None
        self._knob_direction = None
        self._hour_format = None
        self._protocol_version = None
        self._capabilities = None
        self._custom_profile = None
        self._clock_schedule = None
        self._port_protocols = None
        self._max_compatibility = None
        self._ufcs = None
        self._device_identification = None
        self._usb_identities = ()
        self._brand_identities = ()

    @property
    def firmware_version(self) -> str | None:
        return self._firmware_version

    @property
    def error_code(self) -> int | None:
        """Code 1 = over-temperature; bits 1–4 = C1–C4, bit 5 = USB-A."""

        return self._error_code

    @property
    def outputs(self) -> Mapping[OutputId, OutputControl]:
        return MappingProxyType(self._outputs)

    @property
    def clock(self) -> ClockState | None:
        return self._clock

    @property
    def image_time_display(self) -> bool | None:
        """Time shown over a custom clock picture (from theme status only)."""

        return self._image_time_display

    @property
    def screen_timeout(self) -> int | None:
        return self._screen_timeout

    @property
    def charging_mode(self) -> int | None:
        return self._charging_mode

    @property
    def updating_firmware(self) -> bool | None:
        return self._updating_firmware

    @property
    def brightness(self) -> int | None:
        return self._brightness

    @property
    def knob_direction(self) -> int | None:
        return self._knob_direction

    @property
    def hour_format(self) -> int | None:
        return self._hour_format

    @property
    def protocol_version(self) -> int | None:
        return self._protocol_version

    @property
    def capabilities(self) -> Capabilities | None:
        return self._capabilities

    @property
    def custom_profile(self) -> CustomProfile | None:
        return self._custom_profile

    @property
    def clock_schedule(self) -> ClockWindow | None:
        return self._clock_schedule

    @property
    def port_protocols(self) -> tuple[PortProtocol, ...] | None:
        return self._port_protocols

    @property
    def max_compatibility(self) -> bool | None:
        return self._max_compatibility

    @property
    def ufcs(self) -> bool | None:
        return self._ufcs

    @property
    def device_identification(self) -> bool | None:
        return self._device_identification

    @property
    def usb_identities(self) -> tuple[IdentitySlot, ...]:
        return self._usb_identities

    @property
    def brand_identities(self) -> tuple[IdentitySlot, ...]:
        return self._brand_identities

    @property
    def custom_charging_mode(self) -> bool:
        return self._charging_mode == CUSTOM_CHARGING_MODE_CODE

    @property
    def has_fault(self) -> bool:
        """Codes 1–63 are faults; 0 and 64+ are not, as in the official app."""

        return self._error_code is not None and 1 <= self._error_code <= 63

    @property
    def over_temperature(self) -> bool:
        return self._error_code == OVER_TEMPERATURE_CODE

    @property
    def priority_bitmap(self) -> int:
        """Preferred C ports, bit 0 = C1."""

        bitmap = 0
        for index, output in enumerate(USB_C_GROUPS):
            control = self._outputs.get(output)
            if control is not None and control.priority == PRIORITY_PREFERRED:
                bitmap |= 1 << index
        return bitmap

    def supports(self, capability: Capabilities) -> bool:
        return (self._capabilities is not None
                and capability in self._capabilities)

    def output_fault(self, output: OutputId) -> bool:
        if not self.has_fault or self.over_temperature:
            return False
        bit = 1 << (GROUPS.index(output) + 1)
        return bool((self._error_code or 0) & bit)

    def keep_theme_details(self, previous: DeviceSettings) -> None:
        """Carry over what only the ``0x0A02`` theme status reports.

        A snapshot lacks the theme URL, the holiday display and the custom
        picture time display; the URL is kept while the theme is unchanged.
        """

        if self._image_time_display is None:
            self._image_time_display = previous.image_time_display
        clock = self._clock
        old = previous.clock
        if clock is None or old is None:
            return
        current = (clock.theme_code, clock.theme_id)
        if current == (old.theme_code, old.theme_id):
            clock.set_theme_url(old.theme_url)
        clock.set_showing_holiday(old.showing_holiday)

    def apply(self, update: SettingsUpdate) -> None:
        """Merge one change; fields the change does not carry are kept."""

        if isinstance(update, OutputSwitchUpdate):
            self._apply_switch(update)
        elif isinstance(update, CountdownUpdate):
            self._apply_countdown(update)
        elif isinstance(update, ScheduleUpdate):
            self._apply_schedule(update)
        elif isinstance(update, ValueUpdate):
            self._apply_value(update)
        elif isinstance(update, FirmwareVersionUpdate):
            self._firmware_version = update.version
        elif isinstance(update, ClockUpdate):
            self._apply_clock(update)
        elif isinstance(update, ChargingModeUpdate):
            self._apply_charging_mode(update)
        elif isinstance(update, PriorityUpdate):
            self._apply_priority(update)
        elif isinstance(update, ThemeDownloadUpdate):
            self._apply_theme_download(update)

    def _load_snapshot(self, reader: FieldReader) -> None:
        self._firmware_version = decode_version(reader.integer(TAG_FIRMWARE))
        self._error_code = reader.integer(TAG_ERROR_CODE)
        for index, output in enumerate(GROUPS):
            tag = TAG_FIRST_OUTPUT_CONTROL + index
            control = decode_output_control(reader.value(tag))
            if control is not None:
                self._outputs[output] = control
        self._clock = decode_snapshot_clock(reader.value(TAG_CLOCK))

        timeout = reader.byte(TAG_SCREEN_TIMEOUT)
        if timeout is not None:
            self._screen_timeout = timeout & 0x0F
        updating = reader.byte(TAG_UPDATING_FIRMWARE)
        if updating is not None:
            self._updating_firmware = updating == 1
        self._charging_mode = reader.byte(TAG_CHARGING_MODE)
        self._brightness = reader.byte(TAG_BRIGHTNESS)
        self._knob_direction = reader.byte(TAG_KNOB_DIRECTION)
        self._hour_format = reader.byte(TAG_HOUR_FORMAT)
        self._protocol_version = reader.byte(TAG_PROTOCOL_VERSION)
        bits = reader.integer(TAG_CAPABILITIES) or 0
        self._capabilities = Capabilities(bits & 0xFFFF)
        self._usb_identities = decode_identities(reader.value(TAG_USB_IDENTITY))
        brands = reader.value(TAG_BRAND_IDENTITY)
        self._brand_identities = decode_identities(brands)
        self._load_gated_fields(reader)

    def _load_gated_fields(self, reader: FieldReader) -> None:
        if self.supports(Capabilities.CUSTOM_CHARGING_MODE):
            profile = reader.value(TAG_CUSTOM_PROFILE)
            self._custom_profile = decode_custom_profile(profile)
        if self.supports(Capabilities.CLOCK_SCHEDULE):
            window = reader.value(TAG_CLOCK_SCHEDULE)
            self._clock_schedule = decode_clock_window(window)
        if self.supports(Capabilities.PORT_PROTOCOLS):
            protocols = reader.value(TAG_PORT_PROTOCOLS)
            self._port_protocols = decode_port_protocols(protocols)

        compatibility = reader.byte(TAG_MAX_COMPATIBILITY)
        if self.supports(Capabilities.MAX_COMPATIBILITY):
            self._max_compatibility = switch_value(compatibility, on_value=0)
        if self.supports(Capabilities.UFCS_SWITCH):
            self._ufcs = switch_value(reader.byte(TAG_UFCS))
        if self.supports(Capabilities.DEVICE_IDENTIFICATION):
            identification = reader.byte(TAG_DEVICE_IDENTIFICATION)
            self._device_identification = switch_value(identification)

    def _apply_switch(self, update: OutputSwitchUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_switch(update.is_on)

    def _apply_countdown(self, update: CountdownUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_countdown(update.enabled, update.total,
                                  update.remaining)

    def _apply_schedule(self, update: ScheduleUpdate) -> None:
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_schedule(update.slot, update.schedule)

    def _apply_value(self, update: ValueUpdate) -> None:
        if update.field == SettingField.BRIGHTNESS:
            self._brightness = update.value
        elif update.field == SettingField.ERROR_CODE:
            self._error_code = update.value
        elif update.field == SettingField.KNOB_DIRECTION:
            self._knob_direction = update.value
        elif update.field == SettingField.SCREEN_TIMEOUT:
            self._screen_timeout = update.value
        elif update.field == SettingField.HOUR_FORMAT:
            self._hour_format = update.value
        elif update.field == SettingField.MAX_COMPATIBILITY:
            self._max_compatibility = bool(update.value)
        elif update.field == SettingField.DEVICE_IDENTIFICATION:
            self._device_identification = bool(update.value)
        elif update.field == SettingField.UFCS:
            self._ufcs = bool(update.value)

    def _apply_clock(self, update: ClockUpdate) -> None:
        clock = update.clock
        if update.image_time_display is not None:
            self._image_time_display = update.image_time_display
        elif clock.theme_url is None and self._clock is not None:
            clock.set_theme_url(self._clock.theme_url)
        self._clock = clock

    def _apply_charging_mode(self, update: ChargingModeUpdate) -> None:
        self._charging_mode = update.code
        if update.profile is not None:
            self._custom_profile = update.profile
        if update.priority is not None:
            self._apply_priority_bitmap(update.priority)
        if update.code == PORT_PRIORITY_MODE_CODE:
            return
        for output in USB_C_GROUPS:
            control = self._outputs.get(output)
            if control is not None:
                control.set_priority(PRIORITY_OFF)

    def _apply_priority(self, update: PriorityUpdate) -> None:
        if update.bitmap is not None:
            self._apply_priority_bitmap(update.bitmap)
            return
        if update.output is None:
            return
        control = self._outputs.get(update.output)
        if control is not None:
            control.set_priority(PRIORITY_PREFERRED if update.preferred
                                 else PRIORITY_NORMAL)

    def _apply_priority_bitmap(self, bitmap: int) -> None:
        for index, output in enumerate(USB_C_GROUPS):
            control = self._outputs.get(output)
            if control is None:
                continue
            preferred = bitmap & (1 << index)
            control.set_priority(PRIORITY_PREFERRED if preferred
                                 else PRIORITY_NORMAL)

    def _apply_theme_download(self, update: ThemeDownloadUpdate) -> None:
        if update.status != 1:
            return
        clock = self._clock or ClockState(True, False, 0)
        theme_id = clock.theme_id
        if update.theme_id is not None:
            theme_id = update.theme_id
        theme_code = clock.theme_code
        if update.theme_code in THEME_PICTURE_CODES:
            theme_code = update.theme_code
        clock.set_theme(theme_code, theme_id)
        self._clock = clock

    @classmethod
    def from_snapshot(cls, frame: Frame) -> DeviceSettings:
        """Decode every setting of a ``0x0A00`` frame."""

        settings = cls()
        settings._load_snapshot(FieldReader(frame))
        return settings
