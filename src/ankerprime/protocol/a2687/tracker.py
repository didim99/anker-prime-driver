"""Tracks what an A2687 reported and maps it to the shared charger model."""

from collections.abc import Mapping
from datetime import datetime
from typing import TypeVar

from ankerprime.model.enums import (
    DisplayLanguage, FixedAllocation, OutputId, PortId)
from ankerprime.model.product import A2687, Product
from ankerprime.model.state import (
    ChargerSettings, ChargerState, Countdown, DeviceInfo,
    DisplaySettings, OutputState, PortReading)
from ankerprime.protocol.a2687.codes import (
    CABLE_RATINGS, CHARGING_MODE_CODES, CHARGING_PROTOCOLS,
    OUTPUTS, PORTS, SCREEN_ORIENTATION_CODES, SCREEN_TIMEOUT_CODES)
from ankerprime.protocol.a2687.telemetry import (
    DeviceSettings, PortControl, RawPortTelemetry, Report, UsbIdentity)
from ankerprime.protocol.codes import decode_value, lookup


ValueT = TypeVar('ValueT')


class StateTracker:
    """Merges decoded reports and builds ``ChargerState`` snapshots.

    Port readings and controls are replaced per port, settings per field
    (the 1 Hz report carries none of them). The display language and the
    fixed allocation are never reported; the values last written are kept.
    """

    _product: Product

    _ports: dict[PortId, RawPortTelemetry]
    _controls: dict[PortId, PortControl]
    _control_times: dict[PortId, datetime]
    _identities: dict[PortId, UsbIdentity]
    _settings: DeviceSettings
    _language: DisplayLanguage | None
    _fixed_allocation: FixedAllocation | None
    _updated_at: datetime | None

    def __init__(self, product: Product = A2687):
        self._product = product
        self._ports = {}
        self._controls = {}
        self._control_times = {}
        self._identities = {}
        self._settings = DeviceSettings()
        self._language = None
        self._fixed_allocation = None
        self._updated_at = None

    @property
    def settings(self) -> DeviceSettings:
        return self._settings

    @property
    def updated_at(self) -> datetime | None:
        return self._updated_at

    def apply(self, report: Report, received_at: datetime) -> bool:
        """Merge a report; return whether it carried state."""

        if report.empty:
            return False
        self._ports.update(report.ports)
        self._controls.update(report.controls)
        self._control_times.update(dict.fromkeys(report.controls, received_at))
        self._identities.update(report.identities)
        if report.settings is not None:
            self._settings = _merge(self._settings, report.settings)
        self._updated_at = received_at
        return True

    def apply_language(self, language: DisplayLanguage) -> None:
        """Keep an acknowledged language write."""

        self._language = language

    def apply_fixed_allocation(self, allocation: FixedAllocation) -> None:
        """Keep an acknowledged allocation write."""

        self._fixed_allocation = allocation

    def apply_charging_mode(self, code: int) -> None:
        """Keep an acknowledged mode write until the next read-all."""

        self._settings = _merge(self._settings,
                                DeviceSettings(charging_mode=code))

    def build(self, *, info: DeviceInfo | None = None, available: bool,
              energy: Mapping[PortId, float] | None = None) -> ChargerState:
        ports = {port: self._port_reading(port, raw)
                 for port, raw in self._ports.items()}
        outputs = {output: self._output_state(output, port)
                   for output, port in zip(OUTPUTS, PORTS, strict=True)
                   if port in self._ports}
        return ChargerState(self._product, info=info, available=available,
                            ports=ports, outputs=outputs,
                            settings=self._charger_settings(), energy=energy,
                            updated_at=self._updated_at)

    def _port_reading(self, port: PortId,
                      raw: RawPortTelemetry) -> PortReading:
        control = self._controls.get(port)
        cable = None
        protocol = None
        if control is not None:
            cable = lookup(CABLE_RATINGS, control.cable_code)
            # Profiles linger after unplugging; Charker shows them only while
            # power flows.
            if _delivering(raw):
                protocol = lookup(CHARGING_PROTOCOLS, control.profile_code)
        identity = self._identities.get(port)
        vendor_id = None
        product_id = None
        if identity is not None and not identity.empty:
            vendor_id, product_id = identity.vendor_id, identity.product_id
        return PortReading(port, status_code=raw.status, voltage=raw.voltage,
                           current=raw.current, power=raw.power, cable=cable,
                           charging_protocol=protocol,
                           usb_vendor_id=vendor_id, usb_product_id=product_id)

    def _output_state(self, output: OutputId, port: PortId) -> OutputState:
        countdown = None
        control = self._controls.get(port)
        reported_at = self._control_times.get(port)
        if (control is not None and control.countdown_running
                and reported_at is not None):
            countdown = Countdown(control.countdown_total,
                                  control.countdown_remaining, reported_at)
        return OutputState(output, is_on=self._ports[port].is_on,
                           countdown=countdown)

    def _charger_settings(self) -> ChargerSettings:
        settings = self._settings
        timeout = decode_value(SCREEN_TIMEOUT_CODES, settings.screen_timeout)
        orientation = decode_value(SCREEN_ORIENTATION_CODES,
                                   settings.orientation)
        display = DisplaySettings(brightness=settings.brightness,
                                  screen_timeout=timeout,
                                  orientation=orientation,
                                  auto_rotation=settings.auto_rotation,
                                  language=self._language)
        mode = decode_value(CHARGING_MODE_CODES, settings.charging_mode)
        return ChargerSettings(charging_mode=mode,
                               charging_mode_code=settings.charging_mode,
                               fixed_allocation=self._fixed_allocation,
                               display=display)


def _delivering(raw: RawPortTelemetry) -> bool:
    return (raw.is_on and raw.current > PortReading.DELIVERING_MIN_CURRENT
            and raw.voltage > PortReading.DELIVERING_MIN_VOLTAGE)


def _merge(old: DeviceSettings, new: DeviceSettings) -> DeviceSettings:
    return DeviceSettings(
        screen_timeout=_pick(new.screen_timeout, old.screen_timeout),
        brightness=_pick(new.brightness, old.brightness),
        charging_mode=_pick(new.charging_mode, old.charging_mode),
        orientation=_pick(new.orientation, old.orientation),
        auto_rotation=_pick(new.auto_rotation, old.auto_rotation))


def _pick(value: ValueT | None, previous: ValueT | None) -> ValueT | None:
    return previous if value is None else value
