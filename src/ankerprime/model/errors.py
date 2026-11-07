"""Error hierarchy of the library."""

from ankerprime.model.capabilities import Capability


class AnkerPrimeError(Exception):
    """Base class for all library errors."""


class AuthenticationError(AnkerPrimeError):
    """Credentials or token were rejected; user action is required."""


class AccountRefusedError(AuthenticationError):
    """A Bluetooth charger refused the user identity and sends no data.

    The charger is bound to an Anker account; its ID is needed as
    the owner ID.
    """


class ChargerConnectionError(AnkerPrimeError):
    """The charger or the cloud service cannot be reached."""


class ProtocolError(AnkerPrimeError):
    """The charger or the cloud service sent something unexpected."""


class ChargerTimeoutError(AnkerPrimeError, TimeoutError):
    """The charger did not answer in time."""


class InvalidParameterError(AnkerPrimeError, ValueError):
    """A command argument is outside the range the charger accepts."""


class UnknownProductError(AnkerPrimeError, LookupError):
    """The model code does not belong to a supported charger."""


class UnsupportedCapabilityError(AnkerPrimeError):
    """The charger, its firmware or this library does not support a feature."""

    _capability: Capability

    def __init__(self, capability: Capability):
        super().__init__(f'capability not supported: {capability}')
        self._capability = capability

    @property
    def capability(self) -> Capability:
        return self._capability


class WriteRejectedError(AnkerPrimeError):
    """The charger acknowledged a write with a non-success status."""

    _status: int

    def __init__(self, status: int):
        super().__init__(f'write rejected by the charger, status {status}')
        self._status = status

    @property
    def status(self) -> int:
        return self._status


class PictureTransferError(AnkerPrimeError):
    """The charger could not fetch or store a picture."""
