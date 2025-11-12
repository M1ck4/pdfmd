"""Utility helpers for pdfmd.

Provides small, side-effect-free helpers used across modules:
- OS-aware path display for GUI/CLI logs.
- Simple logging and progress callbacks.
- Generic regex/text helpers.
- Lightweight file/time utilities if needed later.
"""
from __future__ import annotations
import os
import sys
import re
from pathlib import Path
from typing import Callable, Optional


# ------------------------- Path display helpers -------------------------
def os_display_path(p: str | Path) -> str:
    """Return a string path using the separator native to the current OS.

    Cosmetic only: does *not* change internal handling by Path.
    Always safe to use in logs or GUI labels.
    """
    try:
        return os.path.normpath(str(p))
    except Exception:
        s = str(p)
        return s.replace("/", os.sep).replace("\\", os.sep)


def safe_join(base: str | Path, *parts: str) -> str:
    """Join path components safely using OS conventions."""
    return str(Path(base).joinpath(*parts))


# ---------------------------- Logging utils ----------------------------
def log(msg: str, out: Optional[Callable[[str], None]] = None):
    """Send a message to stdout or a callback."""
    if out:
        out(msg)
    else:
        print(msg, flush=True)


def progress(percent: float, width: int = 30) -> str:
    """Return a simple progress bar string for CLI."""
    pct = max(0, min(100, int(percent)))
    filled = int(width * pct / 100)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {pct:3d}%"


# -------------------------- Text / regex utils --------------------------
def normalize_punctuation(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("…", "...").replace("–", "-").replace("—", "-")
    return s


def linkify_urls(text: str) -> str:
    url_re = re.compile(r"(https?://[^\s)]+)")
    return url_re.sub(lambda m: f"[{m.group(0)}]({m.group(0)})", text)


def escape_markdown(text: str) -> str:
    special = r"`*_{}[]()#+-.!|>~"
    table = {c: f"\\{c}" for c in special}
    return "".join(table.get(ch, ch) for ch in text)


def truncate(text: str, limit: int = 80) -> str:
    """Return text trimmed to `limit` chars with ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


# -------------------------- Misc small helpers --------------------------
def is_windows() -> bool:
    return os.name == "nt"


def clear_console():
    os.system("cls" if is_windows() else "clear")


def print_error(e: Exception):
    print(f"Error: {e}", file=sys.stderr, flush=True)


__all__ = [
    "os_display_path",
    "safe_join",
    "log",
    "progress",
    "normalize_punctuation",
    "linkify_urls",
    "escape_markdown",
    "truncate",
    "is_windows",
    "clear_console",
    "print_error",
]
