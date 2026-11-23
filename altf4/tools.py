"""Tools available to the workflow roles."""

from pathlib import Path


def read_note(path: Path) -> str:
    """Read a local UTF-8 text note, including files saved with a BOM."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeError as error:
        raise UnicodeError(f"{path}: expected a UTF-8 text file") from error
