"""Text extraction layer for pdfmd.

This module provides a single public function `extract_pages()` that returns a
list of `PageText` objects for the given PDF. It supports three modes:

- Native (PyMuPDF): fast, faithful when the PDF contains real text.
- OCR via Tesseract (optional): render each page → run pytesseract.
- OCR via OCRmyPDF (optional): pre-process the whole PDF with `ocrmypdf`, then
  run the native extractor on the OCR'ed PDF. Useful for scanned PDFs while
  preserving layout and selectable text.

The chosen path is controlled by `Options.ocr_mode`:
  "off" | "auto" | "tesseract" | "ocrmypdf".
When set to "auto", a quick probe examines the first few pages and switches to
OCR if the doc appears scanned.

All dependencies are optional except PyMuPDF. If OCR deps are missing, the
module degrades gracefully and logs a helpful hint.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Callable
import io
import os
import sys
import tempfile
import subprocess

try:
    import fitz  # PyMuPDF (required)
except Exception as e:  # pragma: no cover
    fitz = None

# Optional OCR deps
try:
    import pytesseract  # type: ignore
    # Configure Tesseract path for Windows
    if sys.platform == "win32":
        # Try common installation paths
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for path in common_paths:
            if os.path.isfile(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break
    _HAS_TESS = True
except Exception:
    pytesseract = None  # type: ignore
    _HAS_TESS = False

try:
    from PIL import Image  # type: ignore
    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAS_PIL = False

from .models import PageText, Options
from .utils import log


# --------------------------- Public entry point ---------------------------
DefProgress = Optional[Callable[[int, int], None]]

def extract_pages(pdf_path: str, options: Options, progress_cb: DefProgress = None) -> List[PageText]:
    """Extract pages as PageText according to OCR mode and preview flag.

    progress_cb, if provided, is called as (done_pages, total_pages).
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed. Install with: pip install pymupdf")

    mode = (options.ocr_mode or "off").lower()

    if mode == "off":
        return _extract_native(pdf_path, options, progress_cb)

    if mode == "auto":
        if _needs_ocr_probe(pdf_path):
            log("[extract] Auto: scanned PDF detected.")
            if _HAS_TESS and _HAS_PIL and _tesseract_available():
                log("[extract] Using Tesseract OCR...")
                return _extract_tesseract(pdf_path, options, progress_cb)
            elif _which("ocrmypdf") and _tesseract_available():
                log("[extract] Using OCRmyPDF...")
                return _extract_ocrmypdf_then_native(pdf_path, options, progress_cb)
            else:
                log("[extract] WARNING: Scanned PDF detected but no OCR available!")
                log("[extract] Install Tesseract from: https://github.com/UB-Mannheim/tesseract/wiki")
                log("[extract] Then run: pip install pytesseract pillow")
                log("[extract] Falling back to native extraction (may produce poor results).")
                return _extract_native(pdf_path, options, progress_cb)
        # Otherwise, native path
        return _extract_native(pdf_path, options, progress_cb)

    if mode == "tesseract":
        if not (_HAS_TESS and _HAS_PIL):
            raise RuntimeError(
                "OCR mode 'tesseract' selected but pytesseract/Pillow are not available.\n"
                "Install with: pip install pytesseract pillow\n"
                "And install Tesseract from: https://github.com/UB-Mannheim/tesseract/wiki"
            )
        if not _tesseract_available():
            raise RuntimeError(
                "Tesseract executable not found.\n"
                "Download and install from: https://github.com/UB-Mannheim/tesseract/wiki\n"
                "Make sure to add it to your system PATH during installation."
            )
        return _extract_tesseract(pdf_path, options, progress_cb)

    if mode == "ocrmypdf":
        if not _tesseract_available():
            raise RuntimeError(
                "OCRmyPDF requires Tesseract, but it's not installed.\n"
                "Download from: https://github.com/UB-Mannheim/tesseract/wiki\n"
                "Make sure to add it to your system PATH during installation."
            )
        return _extract_ocrmypdf_then_native(pdf_path, options, progress_cb)

    # Fallback
    return _extract_native(pdf_path, options, progress_cb)


# ------------------------------- Heuristics -------------------------------

def _needs_ocr_probe(pdf_path: str, pages_to_check: int = 3) -> bool:
    """Enhanced probe: detect scanned PDFs by analyzing text density and image coverage.
    
    A PDF is likely scanned if:
    1. Very little extractable text per page
    2. Large images covering most of the page area
    3. Low text density relative to page size
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return False
    
    if doc.page_count == 0:
        return False
    
    total = min(pages_to_check, doc.page_count)
    text_chars = 0
    scanned_indicators = 0
    
    for i in range(total):
        page = doc.load_page(i)
        text = page.get_text("text").strip()
        text_chars += len(text)
        
        # Get page dimensions
        rect = page.rect
        page_area = rect.width * rect.height
        
        # Check for images
        images = page.get_images(full=True)
        if images:
            for img_info in images:
                try:
                    xref = img_info[0]
                    pix = fitz.Pixmap(doc, xref)
                    img_area = pix.width * pix.height
                    
                    # If a single image covers >60% of page and little text
                    if page_area > 0 and (img_area / page_area) > 0.6 and len(text) < 150:
                        scanned_indicators += 1
                        break
                except Exception:
                    # If we can't process the image, skip it
                    continue
        
        # Very low text on a page with images suggests scanning
        if len(text) < 100 and images:
            scanned_indicators += 1
    
    doc.close()
    
    # Decision logic:
    # - Average less than 150 chars per page, OR
    # - Multiple pages show scanned indicators
    avg_chars_per_page = text_chars / total if total > 0 else 0
    return avg_chars_per_page < 150 or scanned_indicators >= 2


# ------------------------------ Native path ------------------------------

def _extract_native(pdf_path: str, options: Options, progress_cb: DefProgress) -> List[PageText]:
    """Extract text using PyMuPDF's native text extraction."""
    doc = fitz.open(pdf_path)
    total = doc.page_count
    
    if total == 0:
        doc.close()
        raise ValueError("PDF has no pages")
    
    limit = total if not options.preview_only else min(3, total)
    out: List[PageText] = []

    for i in range(limit):
        page = doc.load_page(i)
        info = page.get_text("dict")
        out.append(PageText.from_pymupdf(info))
        if progress_cb:
            progress_cb(i + 1, limit)

    doc.close()
    return out


# ------------------------------ OCR: Tesseract ------------------------------

def _extract_tesseract(pdf_path: str, options: Options, progress_cb: DefProgress) -> List[PageText]:
    """Extract text using Tesseract OCR on rendered page images."""
    if not (_HAS_TESS and _HAS_PIL):
        raise RuntimeError("Tesseract/Pillow not available. Install with: pip install pytesseract pillow, and install Tesseract engine.")

    doc = fitz.open(pdf_path)
    total = doc.page_count
    
    if total == 0:
        doc.close()
        raise ValueError("PDF has no pages")
    
    limit = total if not options.preview_only else min(3, total)
    out: List[PageText] = []
    
    # Use 200 DPI for preview mode to save memory/time, 300 for full quality
    dpi = 200 if options.preview_only else 300

    for i in range(limit):
        page = doc.load_page(i)
        
        # Render at higher DPI for better OCR
        pix = page.get_pixmap(dpi=dpi)
        if not hasattr(pix, "tobytes"):
            raise RuntimeError("Unexpected: pixmap missing tobytes()")
        
        png_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(png_bytes))
        
        # Run Tesseract with confidence data
        try:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)  # type: ignore[attr-defined]
            out.append(PageText.from_tesseract_data(data))
        except Exception as e:
            log(f"[extract] Tesseract failed on page {i + 1}: {e}")
            # Add empty page on failure
            out.append(PageText(blocks=[]))
        
        if progress_cb:
            progress_cb(i + 1, limit)

    doc.close()
    return out


# ------------------------------ OCR: OCRmyPDF ------------------------------

def _extract_ocrmypdf_then_native(pdf_path: str, options: Options, progress_cb: DefProgress) -> List[PageText]:
    """Attempt OCRmyPDF; if unavailable or fails, fall back to native.

    This path pre-processes the input with OCR, writes to a temp file, then runs
    the normal native extractor on the OCR'ed PDF to preserve text structure.
    """
    ocrmypdf_bin = _which("ocrmypdf")
    if not ocrmypdf_bin:
        log("[extract] 'ocrmypdf' not found on PATH. Falling back to native extraction.")
        return _extract_native(pdf_path, options, progress_cb)

    with tempfile.TemporaryDirectory(prefix="pdfmd_") as tmp:
        out_pdf = os.path.join(tmp, "ocr.pdf")
        
        # Build command: --force-ocr ensures OCR even if text exists
        # Removed --skip-text as it conflicts with --force-ocr
        cmd = [ocrmypdf_bin, "--force-ocr", pdf_path, out_pdf]
        
        try:
            log("[extract] Running OCRmyPDF (this may take a while)...")
            # Set timeout to 10 minutes (600 seconds) to prevent hanging
            # Capture output for progress logging
            result = subprocess.run(
                cmd, 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                timeout=600,
                text=True
            )
            
            # Log any progress info from stderr
            if result.stderr:
                for line in result.stderr.split('\n'):
                    if line.strip() and any(keyword in line.lower() for keyword in ['page', 'processing', 'ocr']):
                        log(f"[extract] {line.strip()}")
            
            log("[extract] OCRmyPDF completed successfully.")
            return _extract_native(out_pdf, options, progress_cb)
            
        except subprocess.TimeoutExpired:
            log("[extract] OCRmyPDF timed out (>10 minutes). Falling back to native extraction.")
            return _extract_native(pdf_path, options, progress_cb)
        except subprocess.CalledProcessError as e:
            log(f"[extract] OCRmyPDF failed with exit code {e.returncode}. Falling back to native extraction.")
            if e.stderr:
                log(f"[extract] Error details: {e.stderr[:200]}")
            return _extract_native(pdf_path, options, progress_cb)
        except Exception as e:
            log(f"[extract] OCRmyPDF failed: {e}. Falling back to native extraction.")
            return _extract_native(pdf_path, options, progress_cb)


# ------------------------------ Small helpers ------------------------------

def _tesseract_available() -> bool:
    """Check if Tesseract executable is actually available."""
    try:
        if sys.platform == "win32":
            # Check common Windows paths
            common_paths = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
            for path in common_paths:
                if os.path.isfile(path):
                    return True
        
        # Try to run tesseract command
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    except Exception:
        return False


def _which(cmd: str) -> Optional[str]:
    """Cross-platform command finder that respects PATH and PATHEXT."""
    paths = os.environ.get("PATH", "").split(os.pathsep)
    exts = [""]
    
    if os.name == "nt":
        pathext = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";")
        exts = [e.lower() for e in pathext]
    
    for p in paths:
        if not p:  # Skip empty path entries
            continue
            
        candidate = os.path.join(p, cmd)
        
        # On Unix-like systems, check without extension
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        
        # On Windows, try with each extension
        if os.name == "nt":
            base = candidate
            for e in exts:
                cand2 = base + e
                if os.path.isfile(cand2) and os.access(cand2, os.X_OK):
                    return cand2
    
    return None


__all__ = [
    "extract_pages",
]