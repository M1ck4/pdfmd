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

from dataclasses import replace
from typing import List, Optional, Tuple
from collections import Counter
import re

from .models import PageText, Block, Line, Span, Options, median_safe

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


__all__ = [
    "is_all_caps_line",
    "is_mostly_caps",
    "detect_repeating_edges",
    "remove_header_footer",
    "strip_drop_caps_in_page",
    "strip_drop_caps",
    "estimate_body_size",
    "transform_pages",
]
