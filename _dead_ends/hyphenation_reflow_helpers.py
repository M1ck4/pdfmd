"""
Hyphenation & line-reflow helpers for PDF -> Markdown pipelines.
Drop-in, pure-Python. Safe defaults; configurable via kwargs.

* Features:
- Normalizes line-breaks:
    - Normalize CRLF/CR and Unicode LS/PS (U+2028/U+2029) to "\\n" up front so that "\\r?" does not need to be included in pattern
- hyphen unwrap:
    - respects ASCII '-' and Unicode hyphens (U+2010..U+2014)
    - recognizes trailing lower-case characters for merge
- reflow of line breaks
    - Structural guards for lists, headings, blockquotes, code fences, tables, hr lines, and "Label:" lines.
    - Non-breaking abbreviations via explicit set + multi-dot patterns.
    - Using Fixed-width, lookahead-only regexes (= no variable-width lookbehinds to make python re happy).
- code-blocks:
    - Detects fenced code blocks opened by ``` or ~~~ (length >= 3).
    - Skips BOTH hyphen unwrapping and paragraph reflow inside fences.
    - Supports variable fence length; closing fence must match the opener char and length.

* Public API:
- unwrap_hyphenation(text: str, aggressive_hyphen: bool = False, protect_code: bool = True) -> str
- reflow_non_sentence_linebreaks(text: str, protect_code: bool = True) -> str
- two_pass_unwrap(text: str, aggressive_hyphen: bool = False, protect_code: bool = True) -> str
    - NOTE When `protect_code=True` (default), unwrap and reflow are skipped inside fenced code blocks (``` or ~~~, len>=3). Set `protect_code=False` to process text inside fences.

TODO Expose variables to GUI 
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final


#* ================= Normalization =================

def _normalize_newlines(text: str) -> str:
    """Normalize all line separators to '\\n' (LF)."""
    # Replace CRLF and CR
    text = text.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    # Replace Unicode line/paragraph separators
    text = text.replace("\\u2028", "\\n").replace("\\u2029", "\\n")
    return text



#* ================= Abbreviations / tokens =================

_NON_BREAKING_ABBREVS: Final[set[str]] = {
    # English
    "e.g.", "i.e.", "vs.", "etc.", "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.",
    "Sr.", "Jr.", "St.", "No.", "Nos.", "Fig.", "Figs.", "Eq.", "Eqs.",
    "pp.", "p.",
    # German
    "z.B.", "bzw.", "bspw.", "ca.", "usw.", "Nr.", "S.",
}

_MULTI_ABBREV_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:[A-Za-z]\\.){2,}$"),  # U.S., e.g., i.e., z.B.
    re.compile(r"(?:Nr|No|S|Fig|Eq|pp|p)\\.$", re.IGNORECASE),
)

# Cross-line multi-token abbreviations to FORCE join across line boundary.
# NBSP (U+00A0) and NNBSP (U+202F) are allowed around spaces.
_NBSP = "\\u00A0"
_NNBSP = "\\u202F"
_MULTI_ABBREV_CROSSLINE: Final[tuple[re.Pattern[str], ...]] = (
    # z. [NBSP|NNBSP] B.  (common in German)
    re.compile(rf"(?i)\\bz\\.\\s*(?:[{_NBSP}{_NNBSP}])?\\s*\\n\\s*b\\."),
    # u. [NBSP|NNBSP] a.
    re.compile(rf"(?i)\\bu\\.\\s*(?:[{_NBSP}{_NNBSP}])?\\s*\\n\\s*a\\."),
)

#* ================= Hyphen unwrap =================

# ASCII '-' and Unicode hyphens: U+2010..U+2014
_HYPHENS = r"[\\-\\u2010\\u2011\\u2012\\u2013\\u2014]"

_BASIC_HYPHEN_JOIN = re.compile(rf"([A-Za-z]){_HYPHENS}\\s*\\n(?=[a-z])")
_TITLECASE_JOIN = re.compile(rf"([a-z]){_HYPHENS}\\s*\\n(?=[A-Z][a-z])")  # Trans-\\nAtlantic



#* ================= Structure guards =================

_LIST_START = re.compile(
    r"^\\s*(?:[*+-]\\s|\\d+\\.\\s|[A-Za-z]\\)\\s|[IVXLCDM]+\\.\\s)",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^\\s{0,3}#{1,6}\\s")
_BLOCKQUOTE = re.compile(r"^\\s{0,3}>\\s")
_CODE_FENCE_LINE = re.compile(r"^\\s{0,3}(`{3,}|~{3,})")
_TABLE_ROW = re.compile(r"^\\s*\\|")
_HR_LINE = re.compile(r"^\\s*(?:-{3,}|_{3,}|\\*{3,})\\s*$")
_LABEL_LINE = re.compile(r"^\\s*[A-Za-z][\\w /-]{0,24}:\\s*$")

_SENTENCE_END_CORE = ".!?…"
_SENTENCE_TRAILING = "\"»”')]}"
_POSSIBLE_ENDING_PUNCTUATION = tuple(_SENTENCE_END_CORE + _SENTENCE_TRAILING)


@dataclass(frozen=True)
class _Line:
    raw: str

    @property
    def is_blank(self) -> bool:
        return not self.raw.strip()

    @property
    def starts_structural_block(self) -> bool:
        s = self.raw
        return any(
            pat.match(s)
            for pat in (
                _LIST_START,
                _HEADING,
                _BLOCKQUOTE,
                _CODE_FENCE_LINE,
                _TABLE_ROW,
                _HR_LINE,
                _LABEL_LINE,
            )
        )

    def _last_token(self) -> str:
        parts = self.raw.rstrip().split()
        return (parts[-1] if parts else "").strip('"\''"”’)]}»")

    def _matches_multi_abbrev(self, token: str) -> bool:
        return any(p.search(token) for p in _MULTI_ABBREV_PATTERNS)

    def ends_sentence(self) -> bool:
        s = self.raw.rstrip()
        if not s:
            return False
        if s[-1] not in _POSSIBLE_ENDING_PUNCTUATION:
            return False

        token = self._last_token()
        if token.endswith("."):
            if token in _NON_BREAKING_ABBREVS:
                return False
            if self._matches_multi_abbrev(token):
                return False
        return True

#* ================= Fence handling =================

def _split_by_fences(text: str) -> list[tuple[bool, str]]:
    """
    Split text into [(is_code, chunk)] by fenced code blocks (``` / ~~~).
    """
    lines = text.splitlines(keepends=True)
    chunks: list[tuple[bool, str]] = []

    buf: list[str] = []
    in_code = False
    fence_char = ""
    fence_len = 0

    def flush(is_code_block: bool) -> None:
        nonlocal buf
        if buf:
            chunks.append((is_code_block, "".join(buf)))
            buf = []

    for ln in lines:
        m = _CODE_FENCE_LINE.match(ln)
        if m:
            fence_seq = m.group(1)
            char = fence_seq[0]
            count = len(fence_seq)

            if not in_code:
                flush(False)
                in_code = True
                fence_char = char
                fence_len = count
                buf.append(ln)
                continue
            else:
                closing = re.match(rf"^\\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\\s*$", ln)
                if closing:
                    buf.append(ln)
                    flush(True)
                    in_code = False
                    fence_char = ""
                    fence_len = 0
                    continue
                buf.append(ln)
                continue
        else:
            buf.append(ln)

    flush(in_code)
    return chunks

#* ================= Main Functions =================

def unwrap_hyphenation(text: str, aggressive_hyphen: bool = False, protect_code: bool = True) -> str:
    """
    Join words broken by hyphen + newline across wrapped lines.
    """
    text = _normalize_newlines(text)

    if protect_code:
        parts = _split_by_fences(text)
    else:
        parts = [(False, text)]

    out: list[str] = []
    for is_code, chunk in parts:
        if is_code:
            out.append(chunk)
        else:
            tmp = _BASIC_HYPHEN_JOIN.sub(r"\\1", chunk)
            if aggressive_hyphen:
                tmp = _TITLECASE_JOIN.sub(r"\\1", tmp)
            out.append(tmp)
    return "".join(out)


def _force_join_crossline(cur_raw: str, nxt_raw: str) -> bool:
    pair = cur_raw + "\\n" + nxt_raw
    return any(p.search(pair) for p in _MULTI_ABBREV_CROSSLINE)


def reflow_non_sentence_linebreaks(text: str, protect_code: bool = True) -> str:
    """Replace *soft* single newlines with spaces to form natural paragraphs."""
    text = _normalize_newlines(text)

    if protect_code:
        parts = _split_by_fences(text)
    else:
        parts = [(False, text)]

    out: list[str] = []
    for is_code, chunk in parts:
        if is_code:
            out.append(chunk)
            continue

        lines = [_Line(l) for l in chunk.splitlines()]
        buf: list[str] = []
        i = 0
        while i < len(lines):
            cur = lines[i]
            if cur.is_blank or i == len(lines) - 1:
                buf.append(cur.raw)
                i += 1
                continue

            nxt = lines[i + 1]
            if nxt.is_blank or nxt.starts_structural_block:
                buf.append(cur.raw)
                i += 1
                continue

            # Force join for cross-line multi-token abbreviations (e.g., "z.\\nB.")
            if _force_join_crossline(cur.raw, nxt.raw):
                buf.append(cur.raw.rstrip() + " ")
                i += 1
                continue

            if cur.ends_sentence():
                buf.append(cur.raw)
                i += 1
                continue

            buf.append(cur.raw.rstrip() + " ")
            i += 1

        out.append("\\n".join(buf))

    return "".join(out)

#* ================= Main Functions =================

def two_pass_unwrap(text: str, aggressive_hyphen: bool = False, protect_code: bool = True) -> str:
    text = unwrap_hyphenation(text, aggressive_hyphen=aggressive_hyphen, protect_code=protect_code)
    text = reflow_non_sentence_linebreaks(text, protect_code=protect_code)
    return text
