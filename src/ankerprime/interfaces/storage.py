"""Persistent storage for credentials, tokens and energy counters.

The library defines what it stores; embedders only provide a key-value store of
JSON-compatible documents (files for the CLI, Home Assistant's storage helper
for the integration).
"""

from abc import ABC, abstractmethod
from typing import Any


JsonDocument = dict[str, Any]


class DocumentStore(ABC):
    """Asynchronous store of JSON-compatible documents addressed by key."""

    @abstractmethod
    async def load(self, key: str) -> JsonDocument | None:
        """Return the stored document, or None when the key is absent."""

        pass

    @abstractmethod
    async def save(self, key: str, document: JsonDocument) -> None:
        """Store the document, replacing any previous one under the key."""

        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove the document; does nothing when the key is absent."""

        pass
