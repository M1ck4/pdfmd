"""Core data models for pdfmd.

This module defines lightweight, serializable structures that represent the
intermediate text model we pass through the pipeline:

- Span:    A run of text with uniform styling.
- Line:    A sequence of spans that appear on the same baseline.
- Block:   A group of lines (roughly a paragraph or heading candidate).
- PageText:All text blocks for a page.
- Options: User-configurable knobs used by extract/transform/render stages.

We provide static constructors to build PageText from:
  • PyMuPDF ("dict" output)
  • Tesseract (pytesseract.image_to_data dict)

These constructors keep *only* the essentials the rest of the pipeline needs:
text runs and coarse style hints (approx size, bold, italic). Layout geometry is
not preserved beyond what helps basic heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Iterable, Optional, Literal


# ---------------------------- Text structures ----------------------------
@dataclass
class Span:
    text: str
    size: float = 0.0
    bold: bool = False
    italic: bool = False


@dataclass
class Line:
    spans: List[Span] = field(default_factory=list)

    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Block:
    lines: List[Line] = field(default_factory=list)

    def is_empty(self) -> bool:
        for ln in self.lines:
            if any(sp.text.strip() for sp in ln.spans):
                return False
        return True


@dataclass
class PageText:
    blocks: List[Block] = field(default_factory=list)

    # ------------------------ PyMuPDF constructor ------------------------
    @staticmethod
    def from_pymupdf(page_dict: Dict[str, Any]) -> "PageText":
        """Build a PageText from fitz.Page.get_text("dict").

        We extract spans (text, size, bold/italic hints) and group them into
        lines and blocks following the original dict structure.
        """
        def span_style(span: Dict[str, Any]) -> tuple[float, bool, bool, str]:
            txt = span.get("text", "") or ""
            size = float(span.get("size", 0.0) or 0.0)
            flags = int(span.get("flags", 0) or 0)
            font = str(span.get("font", "") or "").lower()
            # Heuristics similar to PyMuPDF semantics
            is_bold = bool(flags & 16) or any(k in font for k in ("bold", "black", "heavy", "semibold"))
            is_italic = bool(flags & 2) or any(k in font for k in ("italic", "oblique"))
            return size, is_bold, is_italic, txt

        blocks: List[Block] = []
        for b in page_dict.get("blocks", []) or []:
            if "lines" not in b:
                # skip images and non-text blocks here
                continue
            lines: List[Line] = []
            for ln in b.get("lines", []) or []:
                spans: List[Span] = []
                for sp in ln.get("spans", []) or []:
                    size, bold, italic, txt = span_style(sp)
                    if not txt:
                        continue
                    spans.append(Span(text=txt, size=size, bold=bold, italic=italic))
                if spans:
                    lines.append(Line(spans=spans))
            if lines:
                blocks.append(Block(lines=lines))
        return PageText(blocks=blocks)

    # ------------------------- Tesseract constructor -------------------------
    @staticmethod
    def from_tesseract_data(data: Dict[str, List[Any]]) -> "PageText":
        """Build PageText from pytesseract.image_to_data() result.

        The data dict contains parallel lists for keys: level, page_num, block_num,
        par_num, line_num, word_num, left, top, width, height, conf, text.

        We group by (block_num, line_num). We do not try to infer bold/italic.
        A crude font-size proxy uses the median of word heights in a line.
        """
        n = len(data.get("text", []))
        if n == 0:
            return PageText()

        # Group indices by (block_num, line_num)
        groups: Dict[tuple[int, int], List[int]] = {}
        for i in range(n):
            txt = data["text"][i] or ""
            if not txt.strip():
                continue
            bno = int(data.get("block_num", [0]*n)[i] or 0)
            lno = int(data.get("line_num", [0]*n)[i] or 0)
            groups.setdefault((bno, lno), []).append(i)

        # Sort groups by block, then line order (by top coordinate if present)
        def group_top(idx_list: List[int]) -> int:
            tops = [int(data.get("top", [0]*n)[i] or 0) for i in idx_list]
            return min(tops) if tops else 0

        ordered_keys = sorted(groups.keys(), key=lambda k: (k[0], group_top(groups[k])))

        blocks: List[Block] = []
        cur_block_key: Optional[int] = None
        cur_block_lines: List[Line] = []

        for (bno, lno) in ordered_keys:
            idxs = groups[(bno, lno)]
            # estimate size by median of heights in this line
            heights = [int(data.get("height", [0]*n)[i] or 0) for i in idxs]
            size_est = float(median_safe(heights)) if heights else 0.0
            # assemble spans in reading order (left coordinate if present)
            idxs_sorted = sorted(idxs, key=lambda i: int(data.get("left", [0]*n)[i] or 0))
            spans = [Span(text=str(data["text"][i]), size=size_est) for i in idxs_sorted]
            line = Line(spans=spans)

            if cur_block_key is None:
                cur_block_key = bno
            if bno != cur_block_key:
                # flush previous block
                if cur_block_lines:
                    blocks.append(Block(lines=cur_block_lines))
                cur_block_lines = [line]
                cur_block_key = bno
            else:
                cur_block_lines.append(line)

        if cur_block_lines:
            blocks.append(Block(lines=cur_block_lines))

        return PageText(blocks=blocks)


# ------------------------------ Options ------------------------------
@dataclass
class Options:
    # Extraction / OCR
    ocr_mode: Literal["off", "auto", "tesseract", "ocrmypdf"] = "off"
    preview_only: bool = False

    # Transform heuristics
    caps_to_headings: bool = True
    defragment_short: bool = True
    heading_size_ratio: float = 1.15
    orphan_max_len: int = 45
    remove_headers_footers: bool = True

    # Rendering / output
    insert_page_breaks: bool = False
    export_images: bool = False


# ------------------------------ Utilities ------------------------------
def median_safe(vals: Iterable[int | float]) -> float:
    xs = [float(v) for v in vals]
    if not xs:
        return 0.0
    xs.sort()
    m = len(xs) // 2
    if len(xs) % 2:
        return xs[m]
    return (xs[m - 1] + xs[m]) / 2.0


__all__ = [
    "Span",
    "Line",
    "Block",
    "PageText",
    "Options",
    "median_safe",
]
