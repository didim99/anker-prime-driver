"""Shortening of identifiers before they reach logs or diagnostics."""

VISIBLE_SUFFIX = 4


def redact(value: str | None) -> str:
    """Keep only the last four characters of an identifier."""

    if not value:
        return '<none>'
    if len(value) <= VISIBLE_SUFFIX:
        return '***'
    return f'***{value[-VISIBLE_SUFFIX:]}'
