"""Charger settings an Anker account keeps."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(init=False)
class AccountSettings:
    """Charger settings the Anker account keeps; None when not reported."""

    _custom_mode_enabled: bool | None
    _max_compatibility: bool | None
    _device_identification: bool | None

    def __init__(self, *, custom_mode_enabled: bool | None = None,
                 max_compatibility: bool | None = None,
                 device_identification: bool | None = None):
        self._custom_mode_enabled = custom_mode_enabled
        self._max_compatibility = max_compatibility
        self._device_identification = device_identification

    @property
    def custom_mode_enabled(self) -> bool | None:
        """The app's Labs switch that makes custom mode available."""

        return self._custom_mode_enabled

    @property
    def max_compatibility(self) -> bool | None:
        return self._max_compatibility

    @property
    def device_identification(self) -> bool | None:
        return self._device_identification

    def replace(self, *, custom_mode_enabled: bool | None = None,
                max_compatibility: bool | None = None,
                device_identification: bool | None = None
                ) -> AccountSettings:
        def pick(new: bool | None, old: bool | None) -> bool | None:
            return old if new is None else new

        return AccountSettings(
            custom_mode_enabled=pick(custom_mode_enabled,
                                     self._custom_mode_enabled),
            max_compatibility=pick(max_compatibility,
                                   self._max_compatibility),
            device_identification=pick(device_identification,
                                       self._device_identification))
