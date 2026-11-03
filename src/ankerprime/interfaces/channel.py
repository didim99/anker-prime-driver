"""Message channel shared by transports and sessions.

A transport is a channel carrying raw payloads (an MQTT message, a BLE
notification). A session wraps a channel and is itself a channel carrying
complete plaintext protocol frames, so device logic never sees encryption or
transport framing.
"""

from abc import ABC, abstractmethod


class ChannelListener(ABC):
    """Receives inbound messages and closure of a channel."""

    @abstractmethod
    def on_message(self, data: bytes) -> None:
        """Handle one inbound message."""

        pass

    @abstractmethod
    def on_closed(self, error: Exception | None) -> None:
        """Handle channel closure; ``error`` is None for a requested close."""

        pass


class Channel(ABC):
    """Bidirectional message channel."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """The channel can send and receive messages."""

        pass

    @abstractmethod
    def set_listener(self, listener: ChannelListener | None) -> None:
        """Set the receiver of inbound messages and closure events."""

        pass

    @abstractmethod
    async def open(self) -> None:
        """Establish the channel; raises a library error on failure."""

        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the channel; does nothing when already closed."""

        pass

    @abstractmethod
    async def send(self, data: bytes) -> None:
        """Send one message; raises a library error when the channel is closed."""

        pass
