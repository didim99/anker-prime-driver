"""Owner-only, atomic file writes for documents holding secrets."""

import os
from pathlib import Path


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def write_private_file(path: Path, text: str) -> None:
    """Write through a temporary file and rename it over the target.

    The file is created with owner-only permissions, so its content is never
    readable by others, not even briefly.
    """

    path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.tmp')
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(temporary, flags, FILE_MODE)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)
    os.chmod(temporary, FILE_MODE)
    os.replace(temporary, path)
