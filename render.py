"""Markdown rendering for pdfmd.

This module converts transformed `PageText` structures into Markdown.
It assumes header/footer removal and drop-cap stripping have already been run
(see `transform.py`).

Main entry: `render_document(pages, options, body_sizes=None, progress_cb=None)`

Key behaviours:
- Applies heading promotion via font size and optional CAPS heuristics.
- Normalizes bullets and numbered lists to proper Markdown formats.
- Repairs hyphenation and unwraps hard line breaks into paragraphs.
- Optionally inserts `---` page break markers between pages.
- Defragments short orphan lines into their preceding paragraphs.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Callable, List, Optional

from .models import Block, Line, PageText, Options
from .utils import normalize_punctuation, linkify_urls, escape_markdown
from .transform import is_all_caps_line, is_mostly_caps


# ---------------------------------------------------------------------------
# Inline helpers
# ---------------------------------------------------------------------------


def _wrap_inline(text: str, bold: bool, italic: bool) -> str:
    """Wrap text with Markdown inline markers for bold/italic.

    Rules:
    - bold + italic: ***text***
    - bold only: **text**
    - italic only: *text*
    - neither: text
    """
    if not text:
        return text

    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


# ---------------------------------------------------------------------------
# Line / paragraph shaping
# ---------------------------------------------------------------------------


def _fix_hyphenation(text: str) -> str:
    """Repair line-wrap hyphenation.

    Typical case in PDFs:
        'hy-\nphen' → 'hyphen'

    We only remove hyphen + newline when it is clearly a wrap.
    """
    return re.sub(r"-\n(\s*)", r"\1", text)


def _unwrap_hard_breaks(lines: List[str]) -> str:
    """Merge wrapped lines into paragraphs. Blank lines remain paragraph breaks.

    Rules:
    - Consecutive non-blank lines are joined with spaces.
    - Blank lines are preserved as paragraph separators.
    - Lines ending with two spaces `"  "` are treated as explicit hard breaks
      (Markdown convention) and terminate the paragraph.
    """
    out: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            out.append(" ".join(buf).strip())
            buf.clear()

    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            flush()
            out.append("")
            continue

        # Explicit hard break: keep line as-is and terminate paragraph buffer.
        if line.endswith("  "):
            buf.append(line)
            flush()
            continue

        buf.append(line)

    flush()
    return "\n".join(out)


def _defragment_orphans(md: str, max_len: int = 45) -> str:
    """Merge short, isolated lines back into the previous paragraph.

    This operates on the final Markdown string, post-assembly.

    Heuristic:
    - If a non-heading line is:
        * sandwiched between blank lines
        * short (<= max_len chars)
        * not already a list item,
      then we append it to the previous non-blank line.
    """
    lines = md.splitlines()
    res: List[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if (
            i > 0
            and i < len(lines) - 1
            and not lines[i - 1].strip()
            and not lines[i + 1].strip()
            and 0 < len(line.strip()) <= max_len
            and not line.strip().startswith("#")
        ):
            # Attach orphan to the previous non-blank line
            j = len(res) - 1
            while j >= 0 and not res[j].strip():
                j -= 1
            if j >= 0:
                res[j] = (res[j].rstrip() + " " + line.strip()).strip()
                i += 2
                continue

        res.append(line)
        i += 1

    return "\n".join(res)


# ---------------------------------------------------------------------------
# Safe joining & footer detection reuse
# ---------------------------------------------------------------------------


def _safe_join_texts(parts: List[str]) -> str:
    """Join adjacent span texts, avoiding accidental double spaces."""
    if not parts:
        return ""
    out = [parts[0]]
    for p in parts[1:]:
        if not p:
            continue
        if out[-1].endswith(" ") or p.startswith(" "):
            out.append(p)
        else:
            out.append(" " + p)
    return "".join(out)


# Reuse footer noise heuristic from transform-like logic here for line-level cleanup.
_FOOTER_DASH_PATTERN = re.compile(r"^[-–—]\s*[-–—]?\s*\d*\s*$")
_FOOTER_PAGENUM_PATTERN = re.compile(r"^\d+\s*$")
_FOOTER_PAGE_LABEL_PATTERN = re.compile(r"^Page\s+\d+\s*$", re.IGNORECASE)


def _is_footer_noise(text: str) -> bool:
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


# ---------------------------------------------------------------------------
# List normalisation
# ---------------------------------------------------------------------------


def _normalize_list_line(ln: str) -> str:
    """Normalize various bullet/numbered prefixes into Markdown list syntax."""
    s = ln.lstrip()
    # Bullet-like prefixes
    if re.match(r"^[•○◦·\-–—]\s+", s):
        s = re.sub(r"^[•○◦·\-–—]\s+", "- ", s)
        return s

    # Numbered: "1. text" or "1) text"
    m_num = re.match(r"^(\d+)[\.\)]\s+", s)
    if m_num:
        num = m_num.group(1)
        s = re.sub(r"^\d+[\.\)]\s+", f"{num}. ", s)
        return s

    # Lettered outlines: "A. text" or "a) text" → bullet
    if re.match(r"^[A-Za-z][\.\)]\s+", s):
        s = re.sub(r"^[A-Za-z][\.\)]\s+", "- ", s)
        return s

    return ln.strip()


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _infer_column_alignments(grid: List[List[str]]) -> List[str]:
    """Return alignment hints per column: 'left', 'right', or 'center'.

    Heuristic:
    - Look at all body rows (skip header).
    - If >70% of non-empty cells in a column are numeric-like (including
      currency / percentages), mark that column as 'right'.
    - Otherwise default to 'left'.
    """
    if not grid or len(grid) < 2:
        return []

    n_cols = len(grid[0])
    alignments: List[str] = []

    for col_idx in range(n_cols):
        numeric_count = 0
        total_count = 0

        for row in grid[1:]:  # Skip header row
            if col_idx >= len(row):
                continue
            cell = (row[col_idx] or "").strip()
            if not cell:
                continue
            total_count += 1

            # Check if numeric-ish (supports commas, $, %, and simple negatives).
            clean = (
                cell.replace(",", "")
                .replace("$", "")
                .replace("%", "")
                .replace("(", "")
                .replace(")", "")
            )
            clean = clean.strip()
            if clean.startswith("+") or clean.startswith("-"):
                clean = clean[1:].strip()
            try:
                float(clean)
                numeric_count += 1
            except ValueError:
                pass

        if total_count == 0:
            alignments.append("left")
        elif numeric_count / total_count > 0.7:
            alignments.append("right")
        else:
            alignments.append("left")

    return alignments


def _render_table_block(block: Block) -> List[str]:
    """Render a table-annotated block (from tables.detect_tables_on_page) as Markdown.

    Expects `block.table_grid` to be a rectangular list-of-lists of strings.
    The first row is treated as a header row. All cell contents are passed
    through Markdown escaping and punctuation normalisation, with smart
    handling of pipe characters so the table stays valid.
    """
    grid = getattr(block, "table_grid", None)
    if not grid:
        return []

    # Ensure all rows have the same number of columns.
    n_cols = max((len(row) for row in grid), default=0)
    if n_cols == 0:
        return []

    norm_rows: List[List[str]] = []
    for row in grid:
        # Pad shorter rows; never truncate content.
        if len(row) < n_cols:
            row = row + [""] * (n_cols - len(row))
        norm_rows.append(row)

    header = norm_rows[0]
    body = norm_rows[1:]

    def fmt_cell(text: str) -> str:
        # Treat lone ASCII pipes as border artifacts from old-style tables.
        raw = (text or "").strip()
        if raw in {"|", "||", "¦"}:
            raw = ""

        # Normalise punctuation and escape Markdown specials.
        raw = normalize_punctuation(raw)
        raw = escape_markdown(raw)

        # Critical: escape any remaining pipe characters so Markdown does
        # not misinterpret them as column separators.
        raw = raw.replace("|", "\\|")

        return raw

    # Infer alignments (left / right) from the data, fallback to left.
    alignments = _infer_column_alignments(norm_rows)
    if not alignments or len(alignments) != n_cols:
        alignments = ["left"] * n_cols

    header_cells = [fmt_cell(c) for c in header]
    header_line = "| " + " | ".join(header_cells) + " |"

    # Build separator row with alignment markers.
    separator_cells: List[str] = []
    for align in alignments:
        if align == "right":
            separator_cells.append("---:")
        elif align == "center":
            separator_cells.append(":---:")
        else:
            separator_cells.append(":---")  # left-align with explicit marker

    separator_line = "| " + " | ".join(separator_cells) + " |"

    body_lines: List[str] = []
    for row in body:
        cells = [fmt_cell(c) for c in row]
        body_lines.append("| " + " | ".join(cells) + " |")

    lines: List[str] = []
    lines.append(header_line)
    lines.append(separator_line)
    lines.extend(body_lines)
    lines.append("")  # blank line after table

    return lines


# ---------------------------------------------------------------------------
# Block → Markdown lines
# ---------------------------------------------------------------------------


def _block_to_lines(
    block: Block,
    body_size: float,
    caps_to_headings: bool,
    heading_size_ratio: float,
) -> List[str]:
    """Convert a Block into a list of Markdown lines.

    We build two parallel views:
      - raw_lines: plain text (no Markdown), for heading detection
      - rendered_lines: text with inline styling (bold/italic), for body output

    Heading detection uses:
      - average span font size vs body_size
      - optional ALL-CAPS / MOSTLY-CAPS heuristic across the block
    """
    # Tables: if this block was annotated as a table in transform.py,
    # render it via the table grid and skip paragraph / heading heuristics.
    if getattr(block, "is_table", False) and getattr(block, "table_grid", None) is not None:
        return _render_table_block(block)

    rendered_lines: List[str] = []
    raw_lines: List[str] = []
    line_sizes: List[float] = []

    for line in block.lines:
        spans = line.spans
        texts_fmt: List[str] = []
        texts_raw: List[str] = []
        sizes: List[float] = []

        # --- Math-aware path: equations module sets dynamic attributes ---
        if getattr(line, "is_math", False):
            kind = getattr(line, "math_kind", "display")
            tex = (getattr(line, "math_tex", "") or "").strip()
            if not tex:
                # Fallback: join raw span text
                tex = "".join(sp.text or "" for sp in spans)

            # Display math: wrap with $$ ... $$ and completely skip
            # escape_markdown, list detection will treat this as a plain line.
            if kind == "display":
                joined_fmt = f"$$\n{tex}\n$$"
                joined_raw = tex
                # Do not contribute to heading sizing; leave `sizes` empty.

            # Inline math: `tex` is the whole line with math segments already
            # normalized; we keep it as-is and again skip escape_markdown so
            # LaTeX commands stay intact.
            else:  # "inline"
                joined_fmt = tex
                joined_raw = tex
                # Use span sizes for body-size estimation if available.
                for sp in spans:
                    if getattr(sp, "size", 0.0):
                        sizes.append(float(sp.size))

        else:
            # Normal text line: escape Markdown and apply inline bold/italic.
            for sp in spans:
                raw_text = sp.text or ""
                texts_raw.append(raw_text)

                esc = escape_markdown(raw_text)
                esc = _wrap_inline(esc, sp.bold, sp.italic)
                texts_fmt.append(esc)

                if getattr(sp, "size", 0.0):
                    sizes.append(float(sp.size))

            joined_fmt = _safe_join_texts(texts_fmt)
            joined_raw = _safe_join_texts(texts_raw)

        if joined_fmt.strip():
            rendered_lines.append(joined_fmt)
            raw_lines.append(joined_raw)
            if sizes:
                line_sizes.append(median(sizes))

    if not rendered_lines:
        return []

    avg_line_size = median(line_sizes) if line_sizes else body_size

    # Use RAW text (no ** or *) for heading heuristics
    block_text_flat = " ".join(raw_lines).strip()

    heading_by_size = avg_line_size >= body_size * heading_size_ratio
    heading_by_caps = caps_to_headings and (
        is_all_caps_line(block_text_flat) or is_mostly_caps(block_text_flat)
    )

    if heading_by_size or heading_by_caps:
        # H1 if much larger than body or if CAPS; otherwise H2
        level = 1 if (avg_line_size >= body_size * 1.6) or heading_by_caps else 2

        # Heading text: use ONLY the first RAW line, not the formatted one
        heading_raw = raw_lines[0]
        heading_text = escape_markdown(heading_raw)
        heading_text = re.sub(r"\s+", " ", heading_text).strip(" -:–—")
        heading_text = normalize_punctuation(heading_text)
        heading_line = f"{'#' * level} {heading_text}"

        # If there's no additional text, just output heading + blank line
        if len(rendered_lines) == 1:
            return [heading_line, ""]

        # Otherwise, render remaining lines as normal paragraph/list text
        tail_text = _fix_hyphenation("\n".join(rendered_lines[1:]))

        lines: List[str] = []
        for ln in tail_text.splitlines():
            if not ln.strip():
                lines.append("")
                continue

            if _is_footer_noise(ln):
                continue

            norm = _normalize_list_line(ln)
            lines.append(norm)

        para = _unwrap_hard_breaks(lines)
        para = normalize_punctuation(para)
        para = linkify_urls(para)

        out: List[str] = [heading_line, ""]
        if para.strip():
            out.append(para)
            out.append("")
        return out

    # ----------------- Normal paragraph path ----------------------------

    para_text = _fix_hyphenation("\n".join(rendered_lines))

    lines: List[str] = []
    for ln in para_text.splitlines():
        if not ln.strip():
            lines.append("")
            continue

        if _is_footer_noise(ln):
            continue

        norm = _normalize_list_line(ln)
        lines.append(norm)

    para = _unwrap_hard_breaks(lines)
    para = normalize_punctuation(para)
    para = linkify_urls(para)
    return [para, ""]


# ---------------------------------------------------------------------------
# Document render
# ---------------------------------------------------------------------------

DefProgress = Optional[Callable[[int, int], None]]


def render_document(
    pages: List[PageText],
    options: Options,
    body_sizes: Optional[List[float]] = None,
    progress_cb: DefProgress = None,
) -> str:
    """Render transformed pages to a Markdown string.

    Args:
        pages: transformed PageText pages
        options: rendering options (see models.Options)
        body_sizes: optional per-page body-size baselines.
                    If not provided, the renderer falls back to 11.0.
        progress_cb: optional progress callback (done, total)
    """
    md_lines: List[str] = []
    total = len(pages)

    for i, page in enumerate(pages):
        body = body_sizes[i] if body_sizes and i < len(body_sizes) else 11.0

        for blk in page.blocks:
            if blk.is_empty():
                continue
            md_lines.extend(
                _block_to_lines(
                    blk,
                    body_size=body,
                    caps_to_headings=options.caps_to_headings,
                    heading_size_ratio=options.heading_size_ratio,
                )
            )

        if options.insert_page_breaks and i < total - 1:
            md_lines.extend(["---", ""])  # page rule

        if progress_cb:
            progress_cb(i + 1, total)

    md = "\n".join(md_lines)
    # Collapse excessive blank lines
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    if options.defragment_short:
        md = _defragment_orphans(md, max_len=options.orphan_max_len)

    # Strip common footer artefacts like trailing "- - 1" or "- -" at end of lines
    md = re.sub(r"\s*-+\s*-+\s*\d*\s*$", "", md, flags=re.MULTILINE)

    # Tighten spaces before punctuation
    md = re.sub(r"\s+([,.;:?!])", r"\1", md)

    return md


__all__ = [
    "render_document",
]
