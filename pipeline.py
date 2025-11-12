"""End‑to‑end conversion pipeline for pdfmd.

Public API:
    pdf_to_markdown(input_pdf: str, output_md: str, options: Options,
                    progress_cb: callable|None = None, log_cb: callable|None = None)

Stages:
    1) Extract → PageText pages   (native or OCR depending on Options)
    2) Transform → clean/annotate pages (drop caps, header/footer removal)
    3) Render → Markdown
    4) Optional: export images to _assets/ and append simple references

Notes:
    - `progress_cb` receives (done, total) at a few milestones; GUI can map this
      to a determinate bar.
    - Image references use forward slashes in Markdown (portable across OSes),
      while all file I/O uses Path/os to be cross‑platform safe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, List, Dict
import os

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from .models import Options
from .extract import extract_pages
from .transform import transform_pages
from .render import render_document
from .utils import log as default_log


DefProgress = Optional[Callable[[int, int], None]]
DefLogger = Optional[Callable[[str], None]]


def _append_image_refs(md: str, page_to_relpaths: Dict[int, List[str]]) -> str:
    if not page_to_relpaths:
        return md
    lines: List[str] = [md.rstrip(), ""]
    for pno in sorted(page_to_relpaths):
        paths = page_to_relpaths[pno]
        if not paths:
            continue
        lines.append(f"**Images from page {pno + 1}:**")
        for i, rel in enumerate(paths, start=1):
            lines.append(f"- ![p{pno + 1}-{i}]({rel})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _export_images(pdf_path: str, output_md: str, options: Options, log_cb: DefLogger = None) -> Dict[int, List[str]]:
    """Export images to an _assets folder next to output_md and return relative paths.

    Returns a mapping: page_index → [relpath, ...].
    """
    if not options.export_images:
        return {}
    if fitz is None:
        if log_cb:
            log_cb("[pipeline] PyMuPDF is not available; cannot export images.")
        return {}

    doc = fitz.open(pdf_path)
    out_path = Path(output_md)
    assets_dir = out_path.with_name(out_path.stem + "_assets")
    assets_dir.mkdir(parents=True, exist_ok=True)

    mapping: Dict[int, List[str]] = {}
    for pno in range(doc.page_count if not options.preview_only else min(3, doc.page_count)):
        page = doc.load_page(pno)
        images = page.get_images(full=True)
        rels: List[str] = []
        for idx, img in enumerate(images, start=1):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            if pix.n > 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            fname = assets_dir / f"img_{pno + 1:03d}_{idx:02d}.png"
            pix.save(str(fname))
            # Markdown wants forward slashes for portability
            rel = assets_dir.name + "/" + fname.name
            rels.append(rel)
        if rels:
            mapping[pno] = rels
    if log_cb and mapping:
        log_cb(f"[pipeline] Exported images to folder: {assets_dir}")
    return mapping


def pdf_to_markdown(input_pdf: str, output_md: str, options: Options,
                    progress_cb: DefProgress = None, log_cb: DefLogger = None) -> None:
    if log_cb is None:
        log_cb = default_log

    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed. Install with: pip install pymupdf")

    # --- Stage 1: Extract ---
    if log_cb:
        log_cb("[pipeline] Extracting text…")
    pages = extract_pages(input_pdf, options, progress_cb=lambda d, t: progress_cb and progress_cb(int(d * 30 / t), 100))

    # --- Stage 2: Transform ---
    if log_cb:
        log_cb("[pipeline] Transforming pages…")
    pages_t, header, footer, body_sizes = transform_pages(pages, options)
    if log_cb and (header or footer):
        log_cb(f"[pipeline] Removed repeating edges → header={header!r}, footer={footer!r}")

    # --- Stage 3: Render ---
    if log_cb:
        log_cb("[pipeline] Rendering Markdown…")
    md = render_document(pages_t, options, body_sizes=body_sizes,
                         progress_cb=lambda d, t: progress_cb and progress_cb(30 + int(d * 60 / t), 100))

    # --- Stage 4: Images (optional) ---
    page_to_rel = _export_images(input_pdf, output_md, options, log_cb=log_cb)
    if page_to_rel:
        md = _append_image_refs(md, page_to_rel)

    # --- Write output ---
    Path(output_md).write_text(md, encoding="utf-8")
    if progress_cb:
        progress_cb(100, 100)
    if log_cb:
        log_cb(f"[pipeline] Saved → {output_md}")


__all__ = [
    "pdf_to_markdown",
]
