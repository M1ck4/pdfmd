"""Text shaping & heuristics for pdfmd.

This module transforms `PageText` structures prior to Markdown rendering.
It is *format-agnostic*: it never emits Markdown. The goal is to clean and
annotate the intermediate model so the renderer can stay simple and
predictable.

Included heuristics:
- Detect and remove repeating headers/footers across pages.
- Strip obvious drop caps (oversized first letter at paragraph start).
- Merge bullet-only lines with following text lines for better list detection.
- Compute body-size baselines used for heading promotion (by size).
- Provide ALL-CAPS helpers used by the renderer for heading promotion.

Transform functions return new `PageText` instances (immutability by copy), so
upstream stages can compare before and after if needed.
"""

from collections import Counter
from dataclasses import replace
from typing import List, Optional, Tuple
import re

from .models import PageText, Block, Line, Span, Options
from .tables import detect_tables_on_page
from .equations import annotate_math_on_page


# --------------------------- CAPS heuristics ---------------------------


def is_all_caps_line(s: str) -> bool:
    """Return True if a line is entirely alphabetic and all caps.

    We ignore digits and punctuation. Whitespace is stripped at both ends.
    """
    s = s.strip()
    if not s:
        return False

    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False

    return all(ch.isupper() for ch in letters)


def is_mostly_caps(s: str, threshold: float = 0.7) -> bool:
    """Return True if a line is mostly capitalized alphabetic characters.

    We count alphabetic characters only and consider the line "mostly caps"
    if the fraction of uppercase letters is >= `threshold`.
    """
    s = s.strip()
    if not s:
        return False

    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= threshold


# --------------------------- Basic line helpers ---------------------------


def _line_text(line: Line) -> str:
    """Join all span texts in a line and strip outer whitespace."""
    return "".join(sp.text for sp in line.spans).strip()


def _first_nonblank_line_text(page: PageText) -> str:
    """Return the text of the first non empty line on a page."""
    for blk in page.blocks:
        for ln in blk.lines:
            t = _line_text(ln)
            if t:
                return t
    return ""


def _last_nonblank_line_text(page: PageText) -> str:
    """Return the text of the last non empty line on a page."""
    for blk in reversed(page.blocks):
        for ln in reversed(blk.lines):
            t = _line_text(ln)
            if t:
                return t
    return ""


# ------------------------- Header/footer detection -------------------------


_HEADER_SIMILARITY_THRESHOLD = 0.8
_FOOTER_SIMILARITY_THRESHOLD = 0.8


def _normalized_text(s: str) -> str:
    """Normalize text for header/footer comparison.

    This strips surrounding whitespace, collapses internal whitespace,
    and lowercases the result.
    """
    s = s.strip()
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).lower()


def _similarity(a: str, b: str) -> float:
    """Return a crude similarity score between 0 and 1 for two strings.

    We compute a token-based Jaccard similarity over words, with a small
    smoothing factor to avoid zero-division.
    """
    na = _normalized_text(a)
    nb = _normalized_text(b)
    if not na or not nb:
        return 0.0
    sa = set(na.split())
    sb = set(nb.split())
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return inter / union


def detect_repeating_edges(
    pages: List[PageText],
) -> Tuple[Optional[str], Optional[str]]:
    """Detect repeating header and footer strings across pages.

    We look at the first and last non empty line of each page and compute a
    majority candidate by normalized text. If enough pages share the same
    normalized header/footer (above similarity threshold), we return the
    canonical string as the detected header/footer.

    Returns:
        (header, footer) where each is either a string or None if no stable
        pattern could be found.
    """
    if not pages:
        return None, None

    header_candidates: List[str] = []
    footer_candidates: List[str] = []

    for p in pages:
        h = _first_nonblank_line_text(p)
        f = _last_nonblank_line_text(p)
        if h:
            header_candidates.append(h)
        if f:
            footer_candidates.append(f)

    if len(header_candidates) < 2 and len(footer_candidates) < 2:
        return None, None

    def _majority(candidates: List[str], threshold: float) -> Optional[str]:
        if not candidates:
            return None

        normalized = [_normalized_text(c) for c in candidates if c.strip()]
        if not normalized:
            return None

        counts = Counter(normalized)
        most_common, freq = counts.most_common(1)[0]
        frac = freq / len(normalized)
        if frac < threshold:
            return None

        # Return one original candidate that matches the normalized winner.
        for c in candidates:
            if _normalized_text(c) == most_common:
                return c
        return None

    header = _majority(header_candidates, _HEADER_SIMILARITY_THRESHOLD)
    footer = _majority(footer_candidates, _FOOTER_SIMILARITY_THRESHOLD)
    return header, footer


# We also apply a couple of pattern-based cleanups for typical page numbers.

_FOOTER_DASH_PATTERN = re.compile(r"^-+\s*\d+\s*-+$")
_FOOTER_PAGENUM_PATTERN = re.compile(r"^\d+$")
_FOOTER_PAGE_LABEL_PATTERN = re.compile(r"^page\s+\d+$", re.IGNORECASE)


def _is_footer_noise(text: str) -> bool:
    """Heuristic for noisy footer or header artifacts at the bottom of a page.

    Examples:
        "- - 1"
        "- - 2"
        "Page 2"
        "---- 3 ----"
    """
    s = text.strip()
    if not s:
        return False
    if _FOOTER_DASH_PATTERN.match(s):
        return True
    if _FOOTER_PAGENUM_PATTERN.match(s):
        return True
    if _FOOTER_PAGE_LABEL_PATTERN.match(s):
        return True
    return False


def remove_header_footer(
    pages: List[PageText], header: Optional[str], footer: Optional[str]
) -> List[PageText]:
    """Return copies of pages with matching header or footer lines removed.

    We compare the joined text of each line to the detected strings and also
    apply some light pattern based cleanup for common footer artifacts like
    "- - 1" or "---- 7 ----".
    """
    if not pages:
        return pages

    header_norm = _normalized_text(header) if header else ""
    footer_norm = _normalized_text(footer) if footer else ""

    out_pages: List[PageText] = []

    for p in pages:
        new_blocks: List[Block] = []

        for blk in p.blocks:
            new_lines: List[Line] = []
            for ln in blk.lines:
                text = _line_text(ln)
                norm = _normalized_text(text)

                # Strip header if it matches (or is very close).
                if header and norm and _similarity(norm, header_norm) >= 0.95:
                    continue

                # Strip footer if it matches (or is noise).
                if footer and norm and _similarity(norm, footer_norm) >= 0.95:
                    continue
                if _is_footer_noise(text):
                    continue

                new_lines.append(ln)

            if new_lines:
                new_blocks.append(replace(blk, lines=new_lines))

        out_pages.append(replace(p, blocks=new_blocks))

    return out_pages


# ------------------------------- Drop caps -------------------------------


def strip_drop_caps_in_page(page: PageText) -> PageText:
    """Strip obvious decorative drop caps from the start of blocks.

    Heuristic: if the first span in the first non blank line of a block is a
    single alphabetic character, and its font size is much larger than the
    median size of the rest of the line, we remove it.
    """
    new_blocks: List[Block] = []

    for blk in page.blocks:
        lines = blk.lines
        if not lines:
            new_blocks.append(blk)
            continue

        modified = False
        new_lines: List[Line] = []

        for ln in lines:
            spans = ln.spans
            if not spans:
                new_lines.append(ln)
                continue

            # Find first non empty span.
            first_idx = None
            for i, sp in enumerate(spans):
                if sp.text.strip():
                    first_idx = i
                    break

            if first_idx is None:
                new_lines.append(ln)
                continue

            first = spans[first_idx]
            rest = spans[first_idx + 1 :]

            if (
                len(first.text.strip()) == 1
                and first.text.strip().isalpha()
                and first.size > 0
                and rest
            ):
                # Compute median size of rest-of-line.
                sizes = [sp.size for sp in rest if sp.size > 0]
                if sizes:
                    sizes_sorted = sorted(sizes)
                    mid = len(sizes_sorted) // 2
                    if len(sizes_sorted) % 2 == 1:
                        median = sizes_sorted[mid]
                    else:
                        median = 0.5 * (
                            sizes_sorted[mid - 1] + sizes_sorted[mid]
                        )

                    if first.size >= 1.5 * median:
                        # Drop-cap detected: remove this span.
                        new_spans = spans[:first_idx] + rest
                        new_ln = replace(ln, spans=new_spans)
                        new_lines.append(new_ln)
                        modified = True
                        continue

            new_lines.append(ln)

        if modified:
            new_blocks.append(replace(blk, lines=new_lines))
        else:
            new_blocks.append(blk)

    return replace(page, blocks=new_blocks)


def strip_drop_caps(pages: List[PageText]) -> List[PageText]:
    """Apply `strip_drop_caps_in_page` to all pages."""
    return [strip_drop_caps_in_page(p) for p in pages]


# --------------------------- Bullet line merging ---------------------------


_BULLET_ONLY_PATTERN = re.compile(r"^[•○◦·\-–—]\s*$")


def _merge_bullet_lines_in_page(page: PageText) -> PageText:
    """Merge bullet only lines with their following text lines.

    Many PDFs encode bullets as one line containing only "•" and the actual
    item text on the next line:

        •

        This is the first bullet item.

    We instead want a single logical line that starts with "• " followed
    by the item text.
    """
    new_blocks: List[Block] = []

    for blk in page.blocks:
        lines = blk.lines
        if not lines:
            new_blocks.append(blk)
            continue

        merged_lines: List[Line] = []
        i = 0
        n = len(lines)

        while i < n:
            ln = lines[i]
            text = _line_text(ln)

            if (
                _BULLET_ONLY_PATTERN.match(text)
                and i + 1 < n
                and _line_text(lines[i + 1])
            ):
                # Bullet-only line followed by a non-empty line.
                bullet_span = ln.spans[0] if ln.spans else None
                nxt = lines[i + 1]
                if bullet_span is None:
                    # Fallback: just keep the next line as-is.
                    merged_lines.append(nxt)
                    i += 2
                    continue

                # Prepend bullet span text + a space to the next line's first span.
                nxt_spans = list(nxt.spans)
                if nxt_spans:
                    first_span = nxt_spans[0]
                    # Preserve style of the next line; only modify text.
                    bullet_text = bullet_span.text.strip() or "•"
                    new_text = f"{bullet_text} {first_span.text.lstrip()}"
                    nxt_spans[0] = replace(first_span, text=new_text)
                else:
                    # No spans? Use the bullet span as a single-span line.
                    nxt_spans = [bullet_span]

                # Combined spans: bullet spans followed by modified next line spans.
                combined_spans = list(ln.spans) + nxt_spans
                merged_ln = replace(nxt, spans=combined_spans)
                merged_lines.append(merged_ln)
                i += 2
                continue

            merged_lines.append(ln)
            i += 1

        new_blocks.append(replace(blk, lines=merged_lines))

    return replace(page, blocks=new_blocks)


def merge_bullet_lines(pages: List[PageText]) -> List[PageText]:
    """Apply `_merge_bullet_lines_in_page` to all pages."""
    return [_merge_bullet_lines_in_page(p) for p in pages]


# ------------------------------ Body sizes ------------------------------


def estimate_body_size(pages: List[PageText]) -> List[float]:
    """Estimate a body text font size per page.

    We collect all non empty span sizes on each page and take the median.
    If a page has no spans with a positive size, we fall back to 11.0.
    """
    body_sizes: List[float] = []

    for p in pages:
        sizes = [
            sp.size
            for blk in p.blocks
            for ln in blk.lines
            for sp in ln.spans
            if sp.size > 0 and (sp.text or "").strip()
        ]

        if not sizes:
            body_sizes.append(11.0)
            continue

        sizes_sorted = sorted(sizes)
        mid = len(sizes_sorted) // 2
        if len(sizes_sorted) % 2 == 1:
            median = sizes_sorted[mid]
        else:
            median = 0.5 * (sizes_sorted[mid - 1] + sizes_sorted[mid])

        body_sizes.append(median)

    return body_sizes


# --------------------------- Main transform API ---------------------------


def transform_pages(
    pages: List[PageText], options: Options
) -> Tuple[List[PageText], Optional[str], Optional[str], List[float]]:
    """Run the standard transform pipeline.

    Returns:
        pages_t        : transformed pages
        header, footer : detected repeating header and footer strings (if any)
        body_sizes     : per page body size baselines
    """
    # 1. Strip decorative drop caps.
    pages_t = strip_drop_caps(pages)

    # 2. Detect repeating header or footer and remove them (if enabled).
    header: Optional[str] = None
    footer: Optional[str] = None

    if options.remove_headers_footers:
        header, footer = detect_repeating_edges(pages_t)
        pages_t = remove_header_footer(pages_t, header, footer)

    # 3. Merge bullet only lines with following text lines for list detection.
    pages_t = merge_bullet_lines(pages_t)

    # 4. Detect simple text tables and annotate blocks.
    annotated_pages: List[PageText] = []
    for page in pages_t:
        detections = detect_tables_on_page(page)
        if not detections:
            annotated_pages.append(page)
            continue

        new_blocks: List[Block] = []
        for idx, blk in enumerate(page.blocks):
            # Attach table metadata if this block was detected as a table.
            for det in detections:
                if det.block_index == idx:
                    # dynamic attributes; renderer will check these later
                    setattr(blk, "is_table", True)
                    setattr(blk, "table_grid", det.grid)
                    break
            new_blocks.append(blk)

        annotated_pages.append(replace(page, blocks=new_blocks))

    pages_t = annotated_pages

    # 5. Detect and annotate math equations and expressions.
    for page in pages_t:
        annotate_math_on_page(page)

    # 6. Compute per page body font size baselines for heading promotion.
    body_sizes = estimate_body_size(pages_t)

    return pages_t, header, footer, body_sizes


__all__ = [
    "is_all_caps_line",
    "is_mostly_caps",
    "detect_repeating_edges",
    "remove_header_footer",
    "strip_drop_caps_in_page",
    "strip_drop_caps",
    "merge_bullet_lines",
    "estimate_body_size",
    "transform_pages",
]
