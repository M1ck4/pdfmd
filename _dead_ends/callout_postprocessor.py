"""
Turn simple "Note:/Tip:/Warning:/Example:" blocks into Obsidian callouts.
Works on final Markdown text. Minimal, regex-based.
"""
from __future__ import annotations
import re

# Map of label -> callout keyword
_MAP = {
    "note": "note",
    "tip": "tip",
    "warning": "warning",
    "example": "example",
    "caution": "caution",
    "info": "info",
}

# Pattern matches:
#   Note:\nParagraph line 1\nParagraph line 2\n\n
# and converts to:
#   > [!note] Note
#   > Paragraph line 1
#   > Paragraph line 2
_BLOCK = re.compile(
    r"(?m)^(?P<label>(?:" + "|".join(k.capitalize() for k in _MAP.keys()) + r")):\s*\n(?P<body>(?:[^\n]*\n)+?)\n(?=\S|\Z)"
)

def _to_callout(m: re.Match) -> str:
    label = m.group("label")
    body = m.group("body").rstrip("\n")
    key = _MAP[label.lower()]
    # Prefix each body line with "> "
    qbody = "\n".join("> " + ln for ln in body.splitlines())
    return f"> [!{key}] {label}\n{qbody}\n\n"

def convert_simple_callouts(md: str) -> str:
    return _BLOCK.sub(_to_callout, md)
