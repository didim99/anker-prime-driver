"""Tracks what an A2345 reported and maps it to the shared charger model."""

from collections.abc import Collection, Iterable, Mapping
from datetime import datetime

from ankerprime.model.account import AccountSettings
from ankerprime.model.capabilities import Capability, Verification
from ankerprime.model.custom import ChargingProfile
from ankerprime.model.enums import OutputId, PortId, Weekday
from ankerprime.model.errors import InvalidParameterError
from ankerprime.model.product import A2345, Product
from ankerprime.model.state import (
    ChargerSettings, ChargerState, ClockSchedule, ClockSettings, Countdown,
    DeviceInfo, DisplaySettings, OutputState, PortReading, Schedule)
from ankerprime.protocol.a2345.codes import (
    CABLE_RATINGS, CHARGING_MODE_CODES, CHARGING_PROTOCOLS,
    CLOCK_THEME_CODES, DEVICE_BRANDS, HOUR_FORMAT_CODES,
    KNOB_DIRECTION_CODES, SCREEN_TIMEOUT_CODES)
from ankerprime.protocol.a2345.device_settings import DeviceSettings
from ankerprime.protocol.a2345.frame import Message
from ankerprime.protocol.a2345.messages import (
    RawPortReading, RealtimeTelemetry, StatusSnapshot, VersionInfo)
from ankerprime.protocol.a2345.settings import (
    THEME_PICTURE_CODES, Capabilities, ClockState,
    ClockWindow, OutputControl, PortSchedule, PriorityCode)
from ankerprime.protocol.a2345.updates import (
    CountdownUpdate, OutputSwitchUpdate, SettingsChange)
from ankerprime.protocol.codes import decode_value, lookup


# Firmware feature bits that gate model capabilities.
GATED_CAPABILITIES = {
    Capability.CLOCK_SCHEDULE: Capabilities.CLOCK_SCHEDULE,
    Capability.CUSTOM_CHARGING_MODE: Capabilities.CUSTOM_CHARGING_MODE,
    Capability.CUSTOM_PROFILES: Capabilities.CUSTOM_CHARGING_MODE,
    Capability.DEVICE_IDENTIFICATION: Capabilities.DEVICE_IDENTIFICATION,
    Capability.MAX_COMPATIBILITY: Capabilities.MAX_COMPATIBILITY,
    Capability.OWN_CLOCK_PICTURES: Capabilities.CUSTOM_IMAGE_TIME_DISPLAY,
    Capability.UFCS: Capabilities.UFCS_SWITCH,
}
PICTURE_PROTOCOL = 1


class StateTracker:
    """Merges decoded messages and builds ``ChargerState`` snapshots.

    Realtime and snapshot messages both carry port readings; the newest wins.
    A snapshot replaces all settings, change messages update them.
    """

    _product: Product

    _ports: dict[PortId, RawPortReading]
    _settings: DeviceSettings | None
    _version: VersionInfo | None
    _control_times: dict[OutputId, datetime]
    _updated_at: datetime | None

    def __init__(self, product: Product = A2345):
        self._product = product
        self._ports = {}
        self._settings = None
        self._version = None
        self._control_times = {}
        self._updated_at = None

    @property
    def settings(self) -> DeviceSettings | None:
        return self._settings

    @property
    def updated_at(self) -> datetime | None:
        return self._updated_at

    def apply(self, message: Message, received_at: datetime) -> bool:
        """Merge a message; return whether it carried state."""

        if isinstance(message, RealtimeTelemetry):
            self._ports.update(message.ports)
        elif isinstance(message, StatusSnapshot):
            self._ports.update(message.ports)
            if self._settings is not None:
                message.settings.keep_theme_details(self._settings)
            self._settings = message.settings
            self._control_times = dict.fromkeys(message.settings.outputs,
                                                received_at)
        elif isinstance(message, VersionInfo):
            self._version = message
        elif isinstance(message, SettingsChange) and message.updates:
            self._apply_change(message, received_at)
        else:
            return False
        self._updated_at = received_at
        return True

    def build(self, *, info: DeviceInfo | None = None, available: bool,
              energy: Mapping[PortId, float] | None = None,
              account: AccountSettings | None = None,
              profiles: Iterable[ChargingProfile] | None = None,
              unavailable: Collection[Capability] = ()) -> ChargerState:
        """Build the model state; ``info`` provides identity the charger lacks.

        ``account`` and ``profiles`` come from the user's account;
        ``unavailable`` removes capabilities the connection cannot offer.
        """

        settings = self._settings
        ports = {port: self._port_reading(port, raw)
                 for port, raw in self._ports.items()}
        outputs = {}
        if settings is not None:
            outputs = {output: self._output_state(output, control, settings)
                       for output, control in settings.outputs.items()}
        over_temperature = None
        error_code = None
        if settings is not None and settings.error_code is not None:
            over_temperature = settings.over_temperature
            error_code = settings.error_code
        return ChargerState(self._product, info=self._device_info(info),
                            available=available,
                            capabilities=self._capabilities(unavailable),
                            ports=ports, outputs=outputs,
                            settings=self._charger_settings(account,
                                                            profiles),
                            over_temperature=over_temperature,
                            error_code=error_code, energy=energy,
                            updated_at=self._updated_at)

    def _apply_change(self, change: SettingsChange,
                      received_at: datetime) -> None:
        if self._settings is None:
            self._settings = DeviceSettings()
        for update in change.updates:
            self._settings.apply(update)
            if isinstance(update, (CountdownUpdate, OutputSwitchUpdate)):
                self._control_times[update.output] = received_at

    def _device_info(self, base: DeviceInfo | None) -> DeviceInfo:
        base = base or DeviceInfo()
        firmware = base.firmware_version
        if self._settings is not None and self._settings.firmware_version:
            firmware = self._settings.firmware_version
        elif self._version is not None:
            firmware = self._version.software_version
        hardware = base.hardware_version
        if self._version is not None:
            hardware = self._version.hardware_version
        return DeviceInfo(serial=base.serial, name=base.name,
                          firmware_version=firmware, hardware_version=hardware,
                          mac_address=base.mac_address)

    def _capabilities(self, unavailable: Collection[Capability]
                      ) -> Mapping[Capability, Verification]:
        capabilities = {capability: verification for capability, verification
                        in self._product.capabilities.items()
                        if capability not in unavailable}
        if self._settings is None or self._settings.capabilities is None:
            return capabilities
        for capability, bit in GATED_CAPABILITIES.items():
            if not self._settings.supports(bit):
                capabilities.pop(capability, None)
        # Picture themes need protocol version 1 or the clock schedule bit,
        # as the official app checks.
        pictures = (self._settings.protocol_version == PICTURE_PROTOCOL
                    or self._settings.supports(Capabilities.CLOCK_SCHEDULE))
        if not pictures:
            capabilities.pop(Capability.CLOCK_PICTURE, None)
        return capabilities

    def _port_reading(self, port: PortId, raw: RawPortReading) -> PortReading:
        vendor_id = None
        product_id = None
        output = self._product.output_of(port)
        if self._settings is not None:
            for slot in self._settings.usb_identities:
                if slot.output == output and not slot.empty:
                    vendor_id, product_id = slot.first, slot.second
        cable = lookup(CABLE_RATINGS, raw.cable)
        protocol = lookup(CHARGING_PROTOCOLS, raw.fast_charge)
        brand = lookup(DEVICE_BRANDS, raw.device_brand)
        return PortReading(port, status_code=raw.status, voltage=raw.voltage,
                           current=raw.current, power=raw.power, cable=cable,
                           charging_protocol=protocol, device_brand=brand,
                           usb_vendor_id=vendor_id, usb_product_id=product_id)

    def _output_state(self, output: OutputId, control: OutputControl,
                      settings: DeviceSettings) -> OutputState:
        countdown = None
        reported_at = self._control_times.get(output, self._updated_at)
        if control.countdown_enabled and reported_at is not None:
            countdown = Countdown(control.countdown_total,
                                  control.countdown_remaining, reported_at)
        preferred = None
        if control.priority != PriorityCode.OFF:
            preferred = control.priority == PriorityCode.PREFERRED
        return OutputState(output, is_on=control.is_on, countdown=countdown,
                           power_on_schedule=_schedule(control.power_on),
                           power_off_schedule=_schedule(control.power_off),
                           preferred=preferred,
                           fault=settings.output_fault(output))

    def _charger_settings(self, account: AccountSettings | None,
                          profiles: Iterable[ChargingProfile] | None
                          ) -> ChargerSettings:
        enabled = account.custom_mode_enabled if account is not None else None
        settings = self._settings
        if settings is None:
            return ChargerSettings(custom_mode_enabled=enabled,
                                   custom_profiles=profiles)
        timeout = decode_value(SCREEN_TIMEOUT_CODES, settings.screen_timeout)
        knob = decode_value(KNOB_DIRECTION_CODES, settings.knob_direction)
        hours = decode_value(HOUR_FORMAT_CODES, settings.hour_format)
        display = DisplaySettings(brightness=settings.brightness,
                                  screen_timeout=timeout, knob_direction=knob,
                                  hour_format=hours)
        clock = None
        if settings.clock is not None:
            clock = _clock_settings(settings.clock, settings.clock_schedule,
                                    settings.image_time_display)
        mode = decode_value(CHARGING_MODE_CODES, settings.charging_mode)
        identification = settings.device_identification
        active = None
        if settings.custom_charging_mode and settings.custom_profile:
            active = settings.custom_profile.number
        return ChargerSettings(charging_mode=mode,
                               charging_mode_code=settings.charging_mode,
                               max_compatibility=settings.max_compatibility,
                               device_identification=identification,
                               ufcs=settings.ufcs,
                               updating_firmware=settings.updating_firmware,
                               display=display, clock=clock,
                               custom_mode_enabled=enabled,
                               active_profile=active,
                               custom_profiles=profiles)


def _schedule(slot: PortSchedule) -> Schedule | None:
    if slot.unset:
        return None
    try:
        return Schedule(slot.enabled, slot.hour, slot.minute,
                        Weekday(slot.repeat_days))
    except InvalidParameterError:
        return None


def _clock_settings(clock: ClockState, window: ClockWindow | None,
                    picture_time: bool | None) -> ClockSettings:
    schedule = None
    if window is not None:
        try:
            schedule = ClockSchedule(window.start_hour, window.start_minute,
                                     window.end_hour, window.end_minute,
                                     Weekday(window.weekdays & 0x7F))
        except InvalidParameterError:
            schedule = None
    theme = decode_value(CLOCK_THEME_CODES, clock.theme_code)
    picture_id = None
    if clock.theme_code in THEME_PICTURE_CODES:
        picture_id = clock.theme_id
    return ClockSettings(clock.is_on, theme=theme,
                         holiday_theme=clock.holiday_enabled,
                         schedule=schedule, picture_id=picture_id,
                         picture_time=picture_time)
