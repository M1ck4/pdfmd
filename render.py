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
from dataclasses import replace
from statistics import median
from typing import Callable, List, Optional

try:  # package-style imports
    from .models import PageText, Block, Line, Span, Options
    from .transform import is_all_caps_line, is_mostly_caps
except Exception:  # script fallback
    from models import PageText, Block, Line, Span, Options  # type: ignore
    from transform import is_all_caps_line, is_mostly_caps  # type: ignore


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

    for ln in lines:
        s = ln.rstrip("\n")
        if s.strip():
            buf.append(s)
        else:
            flush()
            out.append("")
    flush()
    return "\n".join(out)


def normalize_punctuation(text: str) -> str:
    # light normalization for common Unicode punctuation that maps to ASCII
    text = text.replace("\u2013", "-")  # en dash
    text = text.replace("\u2014", "-")  # em dash
    text = text.replace("\u00A0", " ")  # NBSP → space
    return text


def linkify_urls(text: str) -> str:
    # naive URL linker; avoids touching existing Markdown links
    url_re = re.compile(r"(?<!\]\()(https?://[^\s)]+)")
    return url_re.sub(r"<\1>", text)


# --------------------------- Lists & bullets tweaks ---------------------------

_BULLET = re.compile(r"^\s*[•·]\s+")
_NUM = re.compile(r"^\s*(\d+)[\.)]\s+")


def _normalize_list_line(ln: str) -> str:
    ln = _BULLET.sub("- ", ln)
    m = _NUM.match(ln)
    if m:
        num = m.group(1)
        return re.sub(r"^\s*\d+[\.)]\s+", f"{num}. ", ln)
    if re.match(r"^\s*[A-Za-z][\.)]\s+", ln):
        return re.sub(r"^\s*[A-Za-z][\.)]\s+", "- ", ln)
    return ln


# ------------------------------ Orphan defragment ------------------------------

def _defragment_orphans(text: str, max_len: int = 45) -> str:
    lines = text.splitlines()
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
            j = len(res) - 1
            while j >= 0 and not res[j].strip():
                j -= 1
            if j >= 0:
                res[j] = (res[j].strip() + " " + line.strip()).strip()
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
        # Join spans and wrap bold/italic
        pieces: List[str] = []
        sizes: List[float] = []
        for sp in spans:
            text = _wrap_inline(sp.text, sp.bold, sp.italic)
            pieces.append(text)
            sizes.append(float(sp.size))
        joined = "".join(pieces).rstrip()
        joined = _normalize_list_line(joined)
        joined = _fix_hyphenation(joined)
        if joined:
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
        # Keep only first line as heading content
        title = block_text.splitlines()[0].strip()
        if title:
            return [f"# {title}", ""]

    # Not a heading → paragraph flow
    lines = block_text.splitlines()
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
        if progress_cb:
            try:
                progress_cb(i, total)
            except Exception:
                pass

        body_size = body_sizes[i] if body_sizes and i < len(body_sizes) else 11.0

        for block in page.blocks:
            md_lines.extend(
                _block_to_lines(
                    block,
                    body_size=body_size,
                    caps_to_headings=options.caps_to_headings,
                    heading_size_ratio=options.heading_size_ratio,
                )
            )

        if options.insert_page_breaks:
            md_lines.append("\n---\n")

    if progress_cb:
        try:
            progress_cb(total, total)
        except Exception:
            pass

    md = "\n".join(md_lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    if options.defragment_short:
        md = _defragment_orphans(md, max_len=options.orphan_max_len)

    # Apply modern unwrap/reflow passes (respect CLI/GUI toggles)
    try:
        from transform import two_pass_unwrap, unwrap_hyphenation, reflow_non_sentence_linebreaks, convert_simple_callouts
    except Exception:
        # local fallback
        from .transform import two_pass_unwrap, unwrap_hyphenation, reflow_non_sentence_linebreaks, convert_simple_callouts  # type: ignore

    unwrap = getattr(options, "unwrap_hyphens", True)
    reflow  = getattr(options, "reflow_soft_breaks", True)
    protect = getattr(options, "protect_code_blocks", getattr(options, "protect_code", True))
    abbrs   = getattr(options, "non_breaking_abbrevs", None)

    if unwrap or reflow:
        if unwrap and reflow:
            md = two_pass_unwrap(
                md,
                aggressive_hyphen=getattr(options, "aggressive_hyphen", False),
                protect_code=protect,
                non_breaking_abbrevs=abbrs,
            )
        else:
            if unwrap:
                md = unwrap_hyphenation(
                    md,
                    aggressive_hyphen=getattr(options, "aggressive_hyphen", False),
                    protect_code=protect,
                )
            if reflow:
                md = reflow_non_sentence_linebreaks(
                    md,
                    protect_code=protect,
                    non_breaking_abbrevs=abbrs,
                )

    if getattr(options, "enable_callouts", True):
        md = convert_simple_callouts(md, callout_map=getattr(options, "callout_map", None))

    # Tighten spaces before punctuation
    md = re.sub(r"\s+([,.;:?!])", r"\1", md)
    return md


__all__ = [
    "render_document",
]
