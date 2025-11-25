"""Utility helpers for pdfmd.

Provides small, side-effect-free helpers used across modules:
- OS-aware path display for GUI/CLI logs.
- Simple logging and progress callbacks.
- Generic regex/text helpers.
"""

from __future__ import annotations

import os
import sys
import re
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# OS / PATH HELPERS
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    """Return True if running on Windows."""
    return os.name == "nt" or sys.platform.lower().startswith("win")


def os_display_path(p: os.PathLike | str) -> str:
    """Return a user-facing path string with OS-appropriate separators.

    On Windows: backslashes (\\)
    On POSIX:   forward slashes (/)
    """
    s = str(p)
    if not s:
        return s

    if is_windows():
        # Normalize to backslashes
        s = s.replace("/", "\\")
    else:
        # Normalize to forward slashes
        s = s.replace("\\", "/")
    return s


def safe_join(*parts: str | os.PathLike) -> str:
    """Join path parts, skipping empty segments."""
    cleaned = [str(p) for p in parts if p not in (None, "", ".")]
    if not cleaned:
        return ""
    return str(Path(cleaned[0]).joinpath(*cleaned[1:]))


# ---------------------------------------------------------------------------
# LOGGING / PROGRESS
# ---------------------------------------------------------------------------

# These are deliberately simple so they work in CLI and GUI contexts.


def log(message: str) -> None:
    """Print a log message to stderr, prefixed for clarity."""
    text = str(message)
    sys.stderr.write(f"[pdf_to_md] {text}\n")
    sys.stderr.flush()


def progress(done: int, total: int) -> None:
    """Simple textual progress callback.

    Other modules can pass this into long-running operations.
    """
    if total <= 0:
        pct = 0.0
    else:
        pct = (done / total) * 100.0
    sys.stderr.write(f"[pdf_to_md] Progress: {done}/{total} ({pct:.1f}%)\r")
    sys.stderr.flush()
    if done >= total:
        sys.stderr.write("\n")
        sys.stderr.flush()


def clear_console() -> None:
    """Clear the terminal/console screen, best-effort."""
    try:
        if is_windows():
            os.system("cls")
        else:
            os.system("clear")
    except Exception:
        # Never crash on a cosmetic operation.
        pass


def print_error(message: str) -> None:
    """Print an error message to stderr in a consistent format."""
    sys.stderr.write(f"[pdf_to_md:ERROR] {message}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# TEXT NORMALIZATION
# ---------------------------------------------------------------------------


_PUNCT_MAP = {
    # Quotes
    "\u2018": "'",  # ‘
    "\u2019": "'",  # ’
    "\u201c": '"',  # “
    "\u201d": '"',  # ”
    # Dashes
    "\u2013": "-",  # –
    "\u2014": "-",  # —
    # Ellipsis
    "\u2026": "...",  # …
}


def normalize_punctuation(text: str) -> str:
    """Normalize common Unicode punctuation to simpler ASCII forms.

    This keeps the output predictable in Markdown and text editors.
    """
    if not text:
        return text
    out = []
    for ch in text:
        out.append(_PUNCT_MAP.get(ch, ch))
    return "".join(out)


_URL_RE = re.compile(
    r"(?P<url>(https?://[^\s<>]+|www\.[^\s<>]+))",
    re.IGNORECASE,
)


def linkify_urls(text: str) -> str:
    """Wrap bare URLs in Markdown-friendly form.

    Example:
        'See https://example.com' → 'See <https://example.com>'
    """

    def _repl(match: re.Match) -> str:
        url = match.group("url")
        # Avoid double-wrapping if already inside <...>
        if url.startswith("<") and url.endswith(">"):
            return url
        # If it's a www. URL, add scheme for safety
        if url.lower().startswith("www."):
            return f"<https://{url}>"
        return f"<{url}>"

    return _URL_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# MARKDOWN ESCAPING
# ---------------------------------------------------------------------------


def escape_markdown(text: str) -> str:
    """Escape only the minimal set of characters that break Markdown.

    IMPORTANT:
    - We do NOT escape periods, parentheses, hyphens, or '#'.
    - We avoid the old behaviour that produced '\\.' and '\\(' everywhere.
    - We only escape characters that are truly dangerous inside plain text.

    This function is called on raw PDF span text BEFORE we add **bold** or *italic*
    markers in the renderer.
    """
    if not text:
        return text

    # Characters we actually want to escape:
    # - backslash itself
    # - backtick (inline code)
    # - asterisk and underscore (emphasis)
    # - curly braces, brackets, angle brackets, and pipe (tables/links)
    # We intentionally do NOT include:
    #   . (.)  ( )  -  #  !
    specials = set("\\`*_{[]}<>|]")

    out_chars = []
    for ch in text:
        if ch in specials:
            out_chars.append("\\" + ch)
        else:
            out_chars.append(ch)
    return "".join(out_chars)


# ---------------------------------------------------------------------------
# MISC
# ---------------------------------------------------------------------------


def truncate(text: str, max_len: int = 120) -> str:
    """Truncate a string for logging/debug, preserving the end.

    Example:
        truncate("abcdef", 4) → "a..."
    """
    s = str(text)
    if len(s) <= max_len:
        return s
    if max_len <= 3:
        return s[:max_len]
    return s[: max_len - 3] + "..."


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
