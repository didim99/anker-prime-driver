"""``DocumentStore`` keeping one JSON file per key in a directory.

Files are readable by the owner only, since documents may hold tokens and
passwords. Writes are atomic: a crash never leaves a half-written document.
"""

import asyncio
import json
import re
from pathlib import Path

from ankerprime.interfaces.storage import DocumentStore, JsonDocument
from ankerprime.model.errors import InvalidParameterError, ProtocolError
from ankerprime.storage.files import write_private_file


KEY_PATTERN = re.compile(r'[A-Za-z0-9_.-]{1,128}')


class JsonFileStore(DocumentStore):
    """Stores ``<key>.json`` files under one directory."""

    _directory: Path

    def __init__(self, directory: Path):
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    async def load(self, key: str) -> JsonDocument | None:
        return await asyncio.to_thread(self._load, self._path(key))

    async def save(self, key: str, document: JsonDocument) -> None:
        text = json.dumps(document, indent=2, sort_keys=True)
        await asyncio.to_thread(write_private_file, self._path(key), text)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    def _path(self, key: str) -> Path:
        if not KEY_PATTERN.fullmatch(key) or key.startswith('.'):
            raise InvalidParameterError(f'invalid storage key: {key!r}')
        return self._directory / f'{key}.json'

    def _load(self, path: Path) -> JsonDocument | None:
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None
        try:
            document = json.loads(text)
        except ValueError:
            raise ProtocolError(f'corrupt document: {path.name}') from None
        if not isinstance(document, dict):
            raise ProtocolError(f'corrupt document: {path.name}')
        return document
