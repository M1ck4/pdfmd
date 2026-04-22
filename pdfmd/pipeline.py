"""End-to-end conversion pipeline for pdfmd.

Public API:
    pdf_to_markdown(input_pdf: str, output_md: str, options: Options,
                    progress_cb: callable|None = None, log_cb: callable|None = None,
                    pdf_password: str|None = None, debug_tables: bool = False)

Stages:
    1) Extract → PageText pages   (native or OCR depending on Options)
    2) Transform → clean/annotate pages (drop caps, header/footer removal, table detection)
    3) Render → Markdown
    4) Optional: export images to _assets/ and append simple references

Notes:
    - `progress_cb` receives (done, total) at a few milestones; GUI can map this
      to a determinate bar.
    - Image references use forward slashes in Markdown (portable across OSes),
      while all file I/O uses Path/os to be cross-platform safe.
    - Password handling is secure: never logged, never persisted, only used in-memory.
    - Table detection can be debugged with debug_tables=True flag.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple
import io
import os

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from PIL import Image

from .models import Options
from .extract import extract_pages, _open_pdf_with_password
from .transform import transform_pages
from .render import render_document
from .utils import log as default_log


DefProgress = Optional[Callable[[int, int], None]]
DefLogger = Optional[Callable[[str], None]]


def _append_image_refs(md: str, page_to_relpaths: Dict[int, List[str]]) -> str:
    """Append image references to the end of the Markdown document.
    
    Args:
        md: Markdown content
        page_to_relpaths: Mapping of page_index → list of relative image paths
        
    Returns:
        Markdown with image references appended
    """
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


def _normalize_pixmap(pix) -> "fitz.Pixmap":
    """Convert a PyMuPDF Pixmap to RGB with no alpha."""
    if pix.colorspace and pix.colorspace.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    return pix


def _pixmap_to_pil(pix) -> Image.Image:
    """Convert a PyMuPDF Pixmap to a PIL Image."""
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _group_tile_strips(
    entries: List[Tuple["fitz.Pixmap", "fitz.Rect"]],
    tol: float = 2.0,
) -> List[List[int]]:
    """Group images that form vertical tile strips.

    Images are considered part of the same strip when they share the same
    x-span (within *tol* points) and their y-coordinates are consecutive
    (y0 of the next image ≈ y1 of the previous, within *tol*).

    Returns list of groups, each group being a list of indices into *entries*.
    """
    indexed = sorted(range(len(entries)), key=lambda i: (round(entries[i][1].x0), round(entries[i][1].y0)))
    groups: List[List[int]] = []
    for i in indexed:
        _, rect = entries[i]
        if groups:
            last_idx = groups[-1][-1]
            _, prev_rect = entries[last_idx]
            same_x = abs(rect.x0 - prev_rect.x0) < tol and abs(rect.x1 - prev_rect.x1) < tol
            consecutive_y = abs(rect.y0 - prev_rect.y1) < tol
            if same_x and consecutive_y:
                groups[-1].append(i)
                continue
        groups.append([i])
    return groups


def _filter_overlapped(
    entries: List[Tuple["fitz.Pixmap", "fitz.Rect"]],
    overlap_threshold: float = 0.5,
) -> List[int]:
    """Return indices of images to keep, dropping small images that are
    significantly overlapped by a larger image.

    An image is dropped when another image's rect covers more than
    *overlap_threshold* of its area and the other image has a larger area.
    """
    keep = set(range(len(entries)))
    for i in range(len(entries)):
        if i not in keep:
            continue
        ri = entries[i][1]
        area_i = ri.get_area()
        if area_i <= 0:
            continue
        for j in range(len(entries)):
            if j == i or j not in keep:
                continue
            rj = entries[j][1]
            area_j = rj.get_area()
            # Compute intersection
            ix0 = max(ri.x0, rj.x0)
            iy0 = max(ri.y0, rj.y0)
            ix1 = min(ri.x1, rj.x1)
            iy1 = min(ri.y1, rj.y1)
            if ix0 >= ix1 or iy0 >= iy1:
                continue
            inter = (ix1 - ix0) * (iy1 - iy0)
            # Drop j if it's smaller and mostly covered by i
            if area_j < area_i and inter / area_j > overlap_threshold:
                keep.discard(j)
    return sorted(keep)


def _stitch_vertical(pil_images: List[Image.Image]) -> Image.Image:
    """Stitch PIL Images vertically into a single image."""
    total_w = max(im.width for im in pil_images)
    total_h = sum(im.height for im in pil_images)
    merged = Image.new("RGB", (total_w, total_h))
    y = 0
    for im in pil_images:
        merged.paste(im, (0, y))
        y += im.height
    return merged



def _export_images(
    pdf_path: str,
    output_md: str,
    options: Options,
    log_cb: DefLogger = None,
    pdf_password: Optional[str] = None,
) -> Dict[int, List[str]]:
    """Export images to an _assets folder next to output_md and return relative paths.

    Returns a mapping: page_index → [relpath, ...].

    Adjacent tile strips (images sharing the same x-span with consecutive
    y-coordinates) are automatically detected and stitched into a single image.
    """
    if not options.export_images:
        return {}

    if fitz is None:
        if log_cb:
            log_cb("[pipeline] PyMuPDF is not available; cannot export images.")
        return {}

    try:
        doc = _open_pdf_with_password(pdf_path, pdf_password)
    except Exception as e:
        if log_cb:
            log_cb(f"[pipeline] Could not export images: {e}")
        return {}

    try:
        out_path = Path(output_md)
        assets_dir = out_path.with_name(out_path.stem + "_assets")
        assets_dir.mkdir(parents=True, exist_ok=True)

        mapping: Dict[int, List[str]] = {}
        page_count = doc.page_count
        limit = page_count if not options.preview_only else min(3, page_count)

        for pno in range(limit):
            page = doc.load_page(pno)
            images = page.get_images(full=True)

            # Collect pixmaps and their placement rects
            entries: List[Tuple[fitz.Pixmap, fitz.Rect]] = []
            for img in images:
                xref = img[0]
                try:
                    pix = _normalize_pixmap(fitz.Pixmap(doc, xref))
                except Exception as exc:
                    if log_cb:
                        log_cb(f"[pipeline] Skipping image xref={xref} on page {pno + 1}: {exc}")
                    continue
                rects = page.get_image_rects(xref)
                rect = rects[0] if rects else fitz.Rect(0, len(entries) * 1000, 1, len(entries) * 1000 + 1)
                entries.append((pix, rect))

            if not entries:
                continue

            # Drop small images that are mostly overlapped by a larger one
            # (e.g. decorative clip-art layered on top of an illustration)
            keep_indices = _filter_overlapped(entries)
            if len(keep_indices) < len(entries):
                dropped = len(entries) - len(keep_indices)
                entries = [entries[i] for i in keep_indices]
                if log_cb:
                    log_cb(f"[pipeline] Dropped {dropped} overlapped fragment(s) on page {pno + 1}")

            groups = _group_tile_strips(entries)
            rels: List[str] = []
            img_idx = 0

            for group in groups:
                img_idx += 1
                fname = assets_dir / f"img_{pno + 1:03d}_{img_idx:02d}.png"
                try:
                    if len(group) == 1:
                        entries[group[0]][0].save(str(fname))
                    else:
                        pil_images = [_pixmap_to_pil(entries[i][0]) for i in group]
                        merged = _stitch_vertical(pil_images)
                        merged.save(str(fname))
                        if log_cb:
                            log_cb(f"[pipeline] Stitched {len(group)} tiles → {fname.name}")
                except Exception as exc:
                    if log_cb:
                        log_cb(f"[pipeline] Could not save image p{pno + 1}-{img_idx}: {exc}")
                    continue

                rel = assets_dir.name + "/" + fname.name
                rels.append(rel)

            if rels:
                mapping[pno] = rels

        if log_cb and mapping:
            log_cb(f"[pipeline] Exported images to folder: {assets_dir}")

        return mapping

    finally:
        doc.close()


def pdf_to_markdown(
    input_pdf: str,
    output_md: str,
    options: Options,
    progress_cb: DefProgress = None,
    log_cb: DefLogger = None,
    pdf_password: Optional[str] = None,
    debug_tables: bool = False,
) -> None:
    """Convert a PDF to Markdown using the full pdfmd pipeline.
    
    This is the main entry point for PDF to Markdown conversion. It orchestrates
    all stages: extraction, transformation, rendering, and optional image export.
    
    Args:
        input_pdf: Path to input PDF file
        output_md: Path where Markdown output will be written
        options: Conversion options (OCR mode, heading detection, etc.)
        progress_cb: Optional callback for progress updates: (done, total)
        log_cb: Optional callback for log messages
        pdf_password: Optional password for encrypted PDFs (ephemeral)
        debug_tables: Enable debug logging for table detection
        
    Raises:
        RuntimeError: If PyMuPDF is not installed
        ValueError: If PDF has no pages or is invalid
        Various exceptions from extraction, transformation, or rendering stages
        
    Side Effects:
        - Writes Markdown file to output_md
        - May create _assets/ folder if export_images is enabled
        - Calls progress_cb and log_cb if provided
        
    Security Notes:
        - pdf_password is never logged or persisted
        - All processing happens locally
        - Output files are written unencrypted
    """
    if log_cb is None:
        log_cb = default_log

    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed. Install with: pip install pymupdf")

    # --- Stage 1: Extract ---
    if log_cb:
        log_cb("[pipeline] Extracting text…")

    # Map page-level progress into the [0, 30] range of a 0 to 100 scale.
    def _stage1_progress(done_pages: int, total_pages: int) -> None:
        if progress_cb and total_pages > 0:
            pct = int(done_pages * 30 / total_pages)
            progress_cb(pct, 100)

    pages = extract_pages(
        input_pdf,
        options,
        progress_cb=_stage1_progress,
        pdf_password=pdf_password,
    )

    if not pages:
        raise ValueError("PDF extraction produced no pages")

    if progress_cb:
        progress_cb(30, 100)

    # --- Stage 2: Transform ---
    if log_cb:
        log_cb("[pipeline] Transforming pages…")
    
    pages_t, header, footer, body_sizes = transform_pages(
        pages, 
        options,
        debug_tables=debug_tables,
    )
    
    if log_cb and (header or footer):
        log_cb(f"[pipeline] Removed repeating edges → header={header!r}, footer={footer!r}")

    if progress_cb:
        progress_cb(60, 100)

    # --- Stage 3: Render ---
    if log_cb:
        log_cb("[pipeline] Rendering Markdown…")
    
    md = render_document(
        pages_t,
        options,
        body_sizes=body_sizes,
    )

    if progress_cb:
        progress_cb(80, 100)

    # --- Stage 4: Optional image export ---
    if options.export_images:
        if log_cb:
            log_cb("[pipeline] Exporting images…")
        
        page_to_rel = _export_images(
            input_pdf,
            output_md,
            options,
            log_cb=log_cb,
            pdf_password=pdf_password,
        )
        
        if page_to_rel:
            md = _append_image_refs(md, page_to_rel)

    if progress_cb:
        progress_cb(90, 100)

    # --- Write output ---
    if log_cb:
        log_cb("[pipeline] Writing output file…")
    
    try:
        Path(output_md).write_text(md, encoding="utf-8")
    except Exception as e:
        if log_cb:
            log_cb(f"[pipeline] Error writing output: {e}")
        raise

    if progress_cb:
        progress_cb(100, 100)
    
    if log_cb:
        log_cb(f"[pipeline] Saved → {output_md}")


__all__ = [
    "pdf_to_markdown",
]