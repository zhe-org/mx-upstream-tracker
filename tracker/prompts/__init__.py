"""Runtime prompt loading.

Agent prompts live as plain ``.txt`` files alongside this module so they can be
edited without touching Python. :func:`load_prompt` reads them at runtime; the
result is cached per process (prompts do not change during a run).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@cache
def load_prompt(name: str) -> str:
    """Return the text of ``<name>.txt`` from this prompts directory.

    ``name`` is the bare stem (e.g. ``"analyzer_system"``). Raises
    :class:`FileNotFoundError` with a clear message if the file is missing.
    """

    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in _PROMPTS_DIR.glob("*.txt"))) or "none"
        raise FileNotFoundError(f"No prompt {name!r} at {path} (available: {available}).")
    return path.read_text(encoding="utf-8").strip()
