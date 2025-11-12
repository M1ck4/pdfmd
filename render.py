"""Markdown rendering for pdfmd.

This module converts transformed `PageText` structures into Markdown.
It assumes header/footer removal and drop-cap stripping have already been run
(see `transform.py`).

Main entry: `render_document(pages, options, body_sizes=None, progress_cb=None)`
- Applies heading promotion via size and optional ALL‑CAPS heuristics.
- Normalizes bullets/numbered lists.
- Repairs hyphenation and unwraps hard line breaks into paragraphs.
- Optionally inserts `---` page break markers.
- Defragments short orphan lines.
"""
from __future__ import annotations

import re
from statistics import median
from typing import List, Optional, Callable

from .models import PageText, Block, Line, Span, Options
from .utils import normalize_punctuation, linkify_urls, escape_markdown
from .transform import is_all_caps_line, is_mostly_caps

# ------------------------------- Inline wraps -------------------------------

def _wrap_inline(text: str, bold: bool, italic: bool) -> str:
    if not text.strip():
        return text
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


# ---------------------------- Line/para utilities ----------------------------

def _fix_hyphenation(text: str) -> str:
    # remove hyphen + newline breaks introduced by column wraps
    return re.sub(r"-\n(\s*)", r"\1", text)


def _unwrap_hard_breaks(lines: List[str]) -> str:
    """Merge wrapped lines into paragraphs. Blank lines remain paragraph breaks."""
    out, buf = [], []

    def flush():
        if buf:
            out.append(" ".join(buf).strip())
            buf.clear()

    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            flush()
            out.append("")
            continue
        if line.endswith("  "):
            buf.append(line.rstrip())
            flush()
        else:
            buf.append(line.strip())
    flush()
    return "\n".join(out)


def _defragment_orphans(md: str, max_len: int = 45) -> str:
    lines = md.splitlines()
    res = []
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


# ------------------------------ Block → lines ------------------------------

def _block_to_lines(block: Block, body_size: float, caps_to_headings: bool, heading_size_ratio: float) -> List[str]:
    rendered_lines: List[str] = []
    line_sizes: List[float] = []

    for line in block.lines:
        spans = line.spans
        # assemble inline with Markdown escapes and basic style markers
        texts: List[str] = []
        sizes: List[float] = []
        for sp in spans:
            t = escape_markdown(sp.text)
            t = _wrap_inline(t, sp.bold, sp.italic)
            texts.append(t)
            if sp.size:
                sizes.append(float(sp.size))
        joined = "".join(texts)
        if joined.strip():
            rendered_lines.append(joined)
            if sizes:
                line_sizes.append(median(sizes))

    if not rendered_lines:
        return []

    avg_line_size = median(line_sizes) if line_sizes else body_size
    block_text = "\n".join(rendered_lines).strip()

    # Heading heuristics: size and/or caps
    heading_by_size = avg_line_size >= body_size * heading_size_ratio
    heading_by_caps = caps_to_headings and (is_all_caps_line(block_text.replace("\n", " ")) or is_mostly_caps(block_text))

    if heading_by_size or heading_by_caps:
        level = 1 if (avg_line_size >= body_size * 1.6) or heading_by_caps else 2
        single = re.sub(r"\s+", " ", block_text).strip(" -:–—")
        single = normalize_punctuation(single)
        return [f"{'#' * level} {single}", ""]

    # Paragraph(s)
    para_text = _fix_hyphenation("\n".join(rendered_lines))

    lines: List[str] = []
    for ln in para_text.splitlines():
        # bullets
        if re.match(r"^\s*([•○◦·\-–—])\s+", ln):
            ln = re.sub(r"^\s*[•○◦·–—-]\s+", "- ", ln)
            lines.append(ln.strip())
            continue
        # numbered lists
        m_num = re.match(r"^\s*(\d+)[\.)]\s+", ln)
        if m_num:
            num = m_num.group(1)
            ln = re.sub(r"^\s*\d+[\.)]\s+", f"{num}. ", ln)
            lines.append(ln.strip())
            continue
        # lettered outlines → simple bullets
        if re.match(r"^\s*[A-Za-z][\.)]\s+", ln):
            ln = re.sub(r"^\s*[A-Za-z][\.)]\s+", "- ", ln)
            lines.append(ln.strip())
            continue
        lines.append(ln)

    para = _unwrap_hard_breaks(lines)
    para = normalize_punctuation(para)
    para = linkify_urls(para)
    return [para, ""]


# ------------------------------ Document render ------------------------------
DefProgress = Optional[Callable[[int, int], None]]

def render_document(pages: List[PageText], options: Options, body_sizes: Optional[List[float]] = None, progress_cb: DefProgress = None) -> str:
    """Render transformed pages to a Markdown string.

    Args:
        pages: transformed PageText pages
        options: rendering options
        body_sizes: optional per-page body-size baselines. If not provided,
                    the renderer falls back to 11.0.
        progress_cb: optional progress callback (done, total)
    """
    md_lines: List[str] = []
    total = len(pages)

    for i, page in enumerate(pages):
        body = body_sizes[i] if body_sizes and i < len(body_sizes) else 11.0
        for blk in page.blocks:
            md_lines.extend(_block_to_lines(
                blk,
                body_size=body,
                caps_to_headings=options.caps_to_headings,
                heading_size_ratio=options.heading_size_ratio,
            ))
        if options.insert_page_breaks and i < total - 1:
            md_lines.extend(["---", ""])  # page rule
        if progress_cb:
            progress_cb(i + 1, total)

    md = "\n".join(md_lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    if options.defragment_short:
        md = _defragment_orphans(md, max_len=options.orphan_max_len)

    # Tighten spaces before punctuation
    md = re.sub(r"\s+([,.;:?!])", r"\1", md)
    return md


__all__ = [
    "render_document",
]
