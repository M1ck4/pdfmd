"""Command‑line interface for pdfmd.

Usage examples:
  pdfmd input.pdf                 # writes input.md next to PDF
  pdfmd input.pdf -o notes.md     # choose output path
  pdfmd input.pdf --ocr auto      # auto‑detect scanned; use OCR if needed
  pdfmd input.pdf --ocr tesseract --export-images --page-breaks

Exit codes: 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

try:
    from pdfmd.models import Options
    from pdfmd.pipeline import pdf_to_markdown
except ImportError:
    import os, sys
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    from models import Options
    from pipeline import pdf_to_markdown



OCR_CHOICES = ("off", "auto", "tesseract", "ocrmypdf")


def _derive_output_path(input_pdf: Path, explicit_out: Optional[str]) -> Path:
    if explicit_out:
        return Path(explicit_out)
    return input_pdf.with_suffix(".md")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdfmd",
        description="Convert PDF to clean Markdown (with optional OCR)",
    )
    p.add_argument("input", help="input PDF file")
    p.add_argument("-o", "--output", help="output Markdown path (.md)")

    p.add_argument("--ocr", choices=OCR_CHOICES, default="off", help="OCR mode (default: off)")
    p.add_argument("--preview", action="store_true", help="process only first 3 pages")

    p.add_argument("--export-images", action="store_true", help="export page images to <output>_assets/")
    p.add_argument("--page-breaks", action="store_true", help="insert '---' between pages")

    p.add_argument("--keep-edges", action="store_true", help="keep repeating headers/footers (do not remove)")
    p.add_argument("--no-caps-to-headings", dest="caps_to_headings", action="store_false", help="do not promote ALL‑CAPS to headings")
    p.set_defaults(caps_to_headings=True)

    p.add_argument("--no-defrag", dest="defragment", action="store_false", help="disable orphan defragment")
    p.set_defaults(defragment=True)

    p.add_argument("--heading-ratio", type=float, default=1.15, help="≥ body × ratio → heading (default: 1.15)")
    p.add_argument("--orphan-len", type=int, default=45, help="max length (chars) of orphan to merge (default: 45)")

    p.add_argument("--quiet", action="store_true", help="suppress log output")
    p.add_argument("--no-progress", action="store_true", help="suppress progress bar")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    inp = Path(args.input)
    if not inp.exists():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 1
    if inp.suffix.lower() != ".pdf":
        print("Input must be a .pdf file", file=sys.stderr)
        return 1

    outp = _derive_output_path(inp, args.output)

    opts = Options(
        ocr_mode=args.ocr,
        preview_only=bool(args.preview),
        caps_to_headings=bool(args.caps_to_headings),
        defragment_short=bool(args.defragment),
        heading_size_ratio=float(args.heading_ratio),
        orphan_max_len=int(args.orphan_len),
        remove_headers_footers=not bool(args.keep_edges),
        insert_page_breaks=bool(args.page_breaks),
        export_images=bool(args.export_images),
    )

    def log_cb(msg: str):
        if not args.quiet:
            print(msg)

    def progress_cb(done: int, total: int):
        if args.no_progress:
            return
        # Simple single‑line progress bar on stderr
        pct = 0
        try:
            if total > 0:
                pct = int((done / total) * 100)
        except Exception:
            pct = done if 0 <= done <= 100 else 0
        bar_width = 28
        filled = int(bar_width * pct / 100)
        bar = "#" * filled + "-" * (bar_width - filled)
        sys.stderr.write(f"\r[{bar}] {pct:3d}%")
        sys.stderr.flush()
        if pct >= 100:
            sys.stderr.write("\n")

    try:
        pdf_to_markdown(str(inp), str(outp), opts, progress_cb=progress_cb, log_cb=log_cb)
    except Exception as e:
        if not args.no_progress:
            sys.stderr.write("\n")
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
