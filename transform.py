"""Text shaping & heuristics for pdfmd.

This module transforms `PageText` structures prior to Markdown rendering.
It is *format-agnostic*: it never emits Markdown. The goal is to clean and
annotate the intermediate model so the renderer can be simple and predictable.

Included heuristics:
- Detect and remove repeating headers/footers across pages.
- Strip obvious drop caps (oversized first letter) at paragraph start.
- Compute body-size baselines used for heading promotion (by size).
- Utilities for ALL-CAPS detection (used by renderer for heading promotion).

Transform functions return new `PageText` instances (immutability by copy), so
upstream stages can compare before/after if needed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import re
from typing import Final, FrozenSet, Iterable, List, Mapping, Optional, Tuple

try:
    from .models import (
        PageText,
        Block,
        Line,
        Span,
        Options,
        median_safe,
        DEFAULT_NON_BREAKING_ABBREVS,
        DEFAULT_CALLOUT_MAP,
    )
except ImportError:  # Script fallback
    from models import (  # type: ignore
        PageText,
        Block,
        Line,
        Span,
        Options,
        median_safe,
        DEFAULT_NON_BREAKING_ABBREVS,
        DEFAULT_CALLOUT_MAP,
    )

# ---------------------- Text post-processing constants ----------------------

_NBSP = "\u00A0"
_NNBSP = "\u202F"

_DEFAULT_NON_BREAKING_ABBREVS: Final[FrozenSet[str]] = frozenset(DEFAULT_NON_BREAKING_ABBREVS)

_RE_MULTI_ABBREV_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:[A-Za-z]\.){2,}$"),
    re.compile(r"(?:Nr|No|S|Fig|Eq|pp|p)\.$", re.IGNORECASE),
)

_RE_MULTI_ABBREV_CROSSLINE: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(rf"(?i)\bz\.\s*(?:[{_NBSP}{_NNBSP}])?\s*\n\s*b\."),
    re.compile(rf"(?i)\bu\.\s*(?:[{_NBSP}{_NNBSP}])?\s*\n\s*a\."),
)

_HYPHENS = r"[\-\u2010\u2011\u2012\u2013\u2014]"
_RE_BASIC_HYPHEN_JOIN = re.compile(rf"([A-Za-z]){_HYPHENS}\s*\n(?=[a-z])")
_RE_TITLECASE_JOIN = re.compile(rf"([a-z]){_HYPHENS}\s*\n(?=[A-Z][a-z])")

_RE_LIST_START = re.compile(
    r"^\s*(?:[*+-]\s|\d+\.\s|[A-Za-z]\)\s|[IVXLCDM]+\.\s)",
    re.IGNORECASE,
)
_RE_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s")
_RE_BLOCKQUOTE_LINE = re.compile(r"^\s{0,3}>\s")
_RE_CODE_FENCE_LINE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_RE_TABLE_ROW = re.compile(r"^\s*\|")
_RE_HR_LINE = re.compile(r"^\s*(?:-{3,}|_{3,}|\*{3,})\s*$")
_RE_LABEL_LINE = re.compile(r"^\s*[A-Za-z][\w /-]{0,24}:\s*$")

_SENTENCE_END_CORE = ".!?."
_SENTENCE_TRAILING = "\"'”’)]}›»"
_POSSIBLE_ENDING_PUNCTUATION = tuple(_SENTENCE_END_CORE + _SENTENCE_TRAILING)
_SENTENCE_STRIP_CHARS = "\"'”’)]}›»"

_DEFAULT_CALLOUT_MAP: Final[dict[str, str]] = DEFAULT_CALLOUT_MAP
_DEFAULT_CALLOUT_MAP_NORMALIZED: Final[dict[str, str]] = {
    k.lower(): v for k, v in _DEFAULT_CALLOUT_MAP.items()
}
_RE_CALLOUT_BLOCK_DEFAULT = re.compile(
    r"(?m)^(?P<label>(?:" + "|".join(k.capitalize() for k in _DEFAULT_CALLOUT_MAP_NORMALIZED.keys()) + r")):\s*\n"
    r"(?P<body>(?:[^\n]*\n)+?)\n(?=\S|\Z)"
)

# -------------------------- CAPS detection utils --------------------------

def is_all_caps_line(s: str) -> bool:
    core = re.sub(r"[^A-Za-z]+", "", s)
    return bool(core) and core.isupper()


def is_mostly_caps(s: str, threshold: float = 0.75) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= threshold


# --------------------------- Header/Footer utils ---------------------------

def _first_nonblank_line_text(page: PageText) -> str:
    for blk in page.blocks:
        for ln in blk.lines:
            t = "".join(sp.text for sp in ln.spans).strip()
            if t:
                return t
    return ""


def _last_nonblank_line_text(page: PageText) -> str:
    for blk in reversed(page.blocks):
        for ln in reversed(blk.lines):
            t = "".join(sp.text for sp in ln.spans).strip()
            if t:
                return t
    return ""


def detect_repeating_edges(pages: List[PageText], min_pages: int = 3) -> Tuple[Optional[str], Optional[str]]:
    """Detect the most common first and last non-blank lines across pages."""
    heads: Counter[str] = Counter()
    tails: Counter[str] = Counter()
    for p in pages:
        top = _first_nonblank_line_text(p)
        bot = _last_nonblank_line_text(p)
        if top:
            heads[top] += 1
        if bot:
            tails[bot] += 1
    header = next((h for h, c in heads.most_common() if c >= min_pages and h), None)
    footer = next((t for t, c in tails.most_common() if c >= min_pages and t), None)
    return header, footer


def remove_header_footer(pages: List[PageText], header: Optional[str], footer: Optional[str]) -> List[PageText]:
    """Return copies of pages with matching header/footer lines removed exactly.

    We compare the *joined* text of each line to the detected strings. This is
    intentionally strict (exact match) to avoid false positives.
    """
    out: List[PageText] = []
    for p in pages:
        new_blocks: List[Block] = []
        for blk in p.blocks:
            new_lines: List[Line] = []
            for ln in blk.lines:
                joined = "".join(sp.text for sp in ln.spans).strip()
                if header and joined == header:
                    continue
                if footer and joined == footer:
                    continue
                new_lines.append(Line(spans=[replace(sp) for sp in ln.spans]))
            if new_lines:
                new_blocks.append(Block(lines=new_lines))
        out.append(PageText(blocks=new_blocks))
    return out


# ------------------------------- Drop caps -------------------------------

def strip_drop_caps_in_page(page: PageText, ratio: float = 1.6) -> PageText:
    """Remove a leading single-letter span if it's much larger than the next span.

    Many PDFs render decorative paragraph initials as a separate large span.
    We remove it if it's a single character and size >= next.size * ratio.
    """
    new_blocks: List[Block] = []
    for blk in page.blocks:
        new_lines: List[Line] = []
        for ln in blk.lines:
            spans = ln.spans
            if len(spans) >= 2:
                first, second = spans[0], spans[1]
                if len(first.text.strip()) == 1 and first.size >= second.size * ratio:
                    spans = spans[1:]
            new_lines.append(Line(spans=[replace(sp) for sp in spans]))
        new_blocks.append(Block(lines=new_lines))
    return PageText(blocks=new_blocks)


def strip_drop_caps(pages: List[PageText], ratio: float = 1.6) -> List[PageText]:
    return [strip_drop_caps_in_page(p, ratio=ratio) for p in pages]


# ---------------------------- Body size baseline ----------------------------

def estimate_body_size(page: PageText) -> float:
    """Median of span sizes on a page; used as a baseline for heading promotion."""
    sizes: List[float] = []
    for blk in page.blocks:
        for ln in blk.lines:
            for sp in ln.spans:
                if sp.text.strip():
                    sizes.append(float(sp.size))
    return median_safe(sizes) if sizes else 11.0


# ----------------------------- High-level pass -----------------------------

def transform_pages(pages: List[PageText], options: Options) -> Tuple[List[PageText], Optional[str], Optional[str], List[float]]:
    """Run the standard transform pipeline.

    Returns:
        pages_t        : transformed pages
        header, footer : detected repeating header/footer strings (if any)
        body_sizes     : per-page body-size baselines
    """
    pages_t = [PageText(blocks=[Block(lines=[Line(spans=[replace(sp) for sp in ln.spans]) for ln in blk.lines]) for blk in p.blocks]) for p in pages]

    # Drop caps
    pages_t = strip_drop_caps(pages_t)

    # Detect and optionally remove header/footer
    header = footer = None
    if options.remove_headers_footers:
        header, footer = detect_repeating_edges(pages_t, min_pages=3)
        if header or footer:
            pages_t = remove_header_footer(pages_t, header, footer)

    # Body size per page
    body_sizes = [estimate_body_size(p) for p in pages_t]

    return pages_t, header, footer, body_sizes


# --- Hyphenation & Reflow -------------------------------------------------

def _normalize_newlines(text: str) -> str:
    """Normalize CRLF/CR and Unicode LS/PS to '\n'."""
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )


@dataclass(frozen=True)
class _Line:
    raw: str
    non_breaking_abbrevs: FrozenSet[str]
    multi_abbrev_patterns: tuple[re.Pattern[str], ...]

    @property
    def is_blank(self) -> bool:
        return not self.raw.strip()

    @property
    def starts_structural_block(self) -> bool:
        s = self.raw
        return any(
            pat.match(s)
            for pat in (
                _RE_LIST_START,
                _RE_HEADING_LINE,
                _RE_BLOCKQUOTE_LINE,
                _RE_CODE_FENCE_LINE,
                _RE_TABLE_ROW,
                _RE_HR_LINE,
                _RE_LABEL_LINE,
            )
        )

    def _last_token(self) -> str:
        parts = self.raw.rstrip().split()
        token = parts[-1] if parts else ""
        return token.strip(_SENTENCE_STRIP_CHARS)

    def _matches_multi_abbrev(self, token: str) -> bool:
        return any(p.search(token) for p in self.multi_abbrev_patterns)

    def ends_sentence(self) -> bool:
        s = self.raw.rstrip()
        if not s or s[-1] not in _POSSIBLE_ENDING_PUNCTUATION:
            return False

        token = self._last_token()
        if token.endswith("."):
            if token in self.non_breaking_abbrevs:
                return False
            if self._matches_multi_abbrev(token):
                return False
        return True


def _split_by_fences(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_code, chunk) segments according to fenced blocks."""
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
        fence_match = _RE_CODE_FENCE_LINE.match(ln)
        if fence_match:
            fence_seq = fence_match.group(1)
            char = fence_seq[0]
            count = len(fence_seq)

            if not in_code:
                flush(False)
                in_code = True
                fence_char = char
                fence_len = count
                buf.append(ln)
                continue

            closing = re.match(rf"^\s{{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$", ln)
            if closing:
                buf.append(ln)
                flush(True)
                in_code = False
                fence_char = ""
                fence_len = 0
                continue

            buf.append(ln)
            continue

        buf.append(ln)

    flush(in_code)
    return chunks


def _force_join_crossline(cur_raw: str, nxt_raw: str) -> bool:
    pair = cur_raw + "\n" + nxt_raw
    return any(pattern.search(pair) for pattern in _RE_MULTI_ABBREV_CROSSLINE)


def unwrap_hyphenation(text: str, aggressive_hyphen: bool = False, protect_code: bool = True) -> str:
    """Join words split by hyphen + newline, optionally skipping code fences."""
    text = _normalize_newlines(text)
    parts = _split_by_fences(text) if protect_code else [(False, text)]

    out: list[str] = []
    for is_code, chunk in parts:
        if is_code:
            out.append(chunk)
            continue

        tmp = _RE_BASIC_HYPHEN_JOIN.sub(r"\1", chunk)
        if aggressive_hyphen:
            tmp = _RE_TITLECASE_JOIN.sub(r"\1", tmp)
        out.append(tmp)

    return "".join(out)


def reflow_non_sentence_linebreaks(
    text: str,
    protect_code: bool = True,
    *,
    non_breaking_abbrevs: Optional[Iterable[str]] = None,
) -> str:
    """Replace soft single newlines (outside code fences) with spaces."""
    text = _normalize_newlines(text)
    parts = _split_by_fences(text) if protect_code else [(False, text)]
    abbreviations: FrozenSet[str] = (
        frozenset(non_breaking_abbrevs)
        if non_breaking_abbrevs is not None
        else _DEFAULT_NON_BREAKING_ABBREVS
    )

    out: list[str] = []
    for is_code, chunk in parts:
        if is_code:
            out.append(chunk)
            continue

        lines = [_Line(line, abbreviations, _RE_MULTI_ABBREV_PATTERNS) for line in chunk.splitlines()]
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

        out.append("\n".join(buf))

    return "".join(out)


def two_pass_unwrap(
    text: str,
    aggressive_hyphen: bool = False,
    protect_code: bool = True,
    *,
    non_breaking_abbrevs: Optional[Iterable[str]] = None,
) -> str:
    """Run hyphen unwrap followed by soft line reflow."""
    text = unwrap_hyphenation(text, aggressive_hyphen=aggressive_hyphen, protect_code=protect_code)
    return reflow_non_sentence_linebreaks(
        text,
        protect_code=protect_code,
        non_breaking_abbrevs=non_breaking_abbrevs,
    )


# --- Callouts --------------------------------------------------------------

def _build_callout_block(label_map: Mapping[str, str]) -> re.Pattern[str]:
    labels = "|".join(k.capitalize() for k in label_map.keys())
    if not labels:
        # With no labels there's nothing to rewrite; use a regex that never matches.
        return re.compile(r"(?!x)")
    return re.compile(
        r"(?m)^(?P<label>(?:" + labels + r")):\s*\n(?P<body>(?:[^\n]*\n)+?)\n(?=\S|\Z)"
    )


def _callout_replacement(match: re.Match[str], label_map: Mapping[str, str]) -> str:
    label = match.group("label")
    body = match.group("body").rstrip("\n")
    key = label_map.get(label.lower())
    if not key:
        return match.group(0)

    quoted_body = "\n".join("> " + line for line in body.splitlines())
    return f"> [!{key}] {label}\n{quoted_body}\n\n"


def convert_simple_callouts(text: str, callout_map: Optional[Mapping[str, str]] = None) -> str:
    """Turn leading 'Label:\nBody' blocks into Obsidian-style callouts."""
    if callout_map is None:
        mapping: Mapping[str, str] = _DEFAULT_CALLOUT_MAP_NORMALIZED
        pattern = _RE_CALLOUT_BLOCK_DEFAULT
    else:
        normalized = {k.lower(): v for k, v in callout_map.items() if k}
        if not normalized:
            return text
        mapping = normalized
        pattern = _build_callout_block(mapping)
    return pattern.sub(lambda m: _callout_replacement(m, mapping), text)


__all__ = [
    "is_all_caps_line",
    "is_mostly_caps",
    "detect_repeating_edges",
    "remove_header_footer",
    "strip_drop_caps_in_page",
    "strip_drop_caps",
    "estimate_body_size",
    "transform_pages",
    "unwrap_hyphenation",
    "reflow_non_sentence_linebreaks",
    "two_pass_unwrap",
    "convert_simple_callouts",
]
