"""Command-line interface for pdfmd.

Usage examples:
  pdfmd input.pdf                 # writes input.md next to PDF
  pdfmd input.pdf -o notes.md     # choose output path
  pdfmd input.pdf --ocr auto      # auto-detect scanned; use OCR if needed
  pdfmd input.pdf --ocr tesseract --export-images --page-breaks

Exit codes:
  0 on success
  1 on error
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Optional

from .models import Options
from .pipeline import pdf_to_markdown


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfmd",
        description="Convert PDFs to clean Markdown (Obsidian-ready). "
                    "Runs fully offline; no uploads or tracking."
    )

    parser.add_argument(
        "input",
        metavar="INPUT_PDF",
        help="Path to the input PDF file."
    )

    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_MD",
        help="Path to the output Markdown file. "
             "Defaults to INPUT with .md extension."
    )

    parser.add_argument(
        "--ocr",
        choices=["off", "auto", "tesseract", "ocrmypdf"],
        default="off",
        help=(
            "OCR mode: "
            "'off' (default) = extract text only; "
            "'auto' = detect scanned pages and OCR as needed; "
            "'tesseract' = force Tesseract OCR; "
            "'ocrmypdf' = use ocrmypdf pipeline."
        ),
    )

    parser.add_argument(
        "--export-images",
        action="store_true",
        help="Export page images to an _assets/ folder and append Markdown references."
    )

    parser.add_argument(
        "--page-breaks",
        action="store_true",
        help="Insert explicit page break markers between pages in the output."
    )

    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Run extraction and transformation but do not write an output file."
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the progress bar."
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress log messages; only show errors."
    )

    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version information and exit."
    )

    return parser


def _make_options(args: argparse.Namespace) -> Options:
    """Map CLI arguments to an Options instance."""
    opts = Options()
    opts.ocr_mode = args.ocr
    opts.preview_only = args.preview_only
    opts.insert_page_breaks = args.page_breaks
    opts.export_images = args.export_images
    return opts


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Lazy import to avoid circulars if anything shifts later.
    from . import __version__ as _version

    if args.version:
        print(f"pdfmd { _version }")
        return 0

    inp = Path(args.input).expanduser()
    if not inp.is_file():
        print(f"Error: input file not found: {inp}", file=sys.stderr)
        return 1

    if args.output:
        outp = Path(args.output).expanduser()
    else:
        outp = inp.with_suffix(".md")

    opts = _make_options(args)

    def log_cb(msg: str) -> None:
        if args.quiet:
            return
        print(msg, file=sys.stderr)

    def progress_cb(done: int, total: int) -> None:
        if args.no_progress:
            return
        # Simple single-line progress bar on stderr
        pct = 0
        try:
            if total > 0:
                pct = int((done / total) * 100)
        except Exception:
            # Fallback: treat 'done' as percent if it looks like one
            pct = done if 0 <= done <= 100 else 0

        bar_width = 28
        filled = int(bar_width * pct / 100)
        bar = "#" * filled + "-" * (bar_width - filled)
        sys.stderr.write(f"\r[{bar}] {pct:3d}%")
        sys.stderr.flush()

    def run_once(pdf_password: Optional[str]) -> None:
        """Single conversion attempt, optionally with a password.

        pdf_password is kept local, never logged or persisted.
        """
        pdf_to_markdown(
            str(inp),
            str(outp),
            opts,
            progress_cb=progress_cb,
            log_cb=log_cb,
            pdf_password=pdf_password,  # will be added in pipeline.py
        )

    # Password handling: keep it local, never persisted.
    password: Optional[str] = None
    try:
        try:
            # First attempt: no password (or whatever we have).
            run_once(password)
        except Exception as e:
            msg = str(e)
            lower = msg.lower()

            # Heuristic: detect password / encryption errors.
            password_keywords = [
                "password required",
                "password is required",
                "incorrect pdf password",
                "wrong password",
                "cannot decrypt",
                "encrypted",
            ]
            needs_password = any(kw in lower for kw in password_keywords)

            if not needs_password:
                if not args.no_progress:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                print(f"Error: {e}", file=sys.stderr)
                return 1

            # At this point we believe a password is needed / incorrect.
            if not sys.stdin.isatty():
                if not args.no_progress:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                print(
                    "Error: PDF is password protected and interactive input "
                    "is not available.",
                    file=sys.stderr,
                )
                return 1

            # Ask the user once for a password without echo.
            try:
                if not args.no_progress:
                    sys.stderr.write("\n")  # finish any current progress line
                    sys.stderr.flush()
                password = getpass.getpass(
                    "PDF is password protected. Enter password (input will be hidden): "
                )
            except Exception as e_input:
                print(f"Error reading password: {e_input}", file=sys.stderr)
                return 1

            # Second attempt with provided password
            try:
                run_once(password)
            except Exception as e2:
                if not args.no_progress:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                print(f"Error: {e2}", file=sys.stderr)
                return 1

        # If we reach here, success.
        if not args.no_progress:
            sys.stderr.write("\n")
            sys.stderr.flush()
        return 0

    finally:
        # Best-effort hygiene: drop any reference to the password.
        password = None


if __name__ == "__main__":
    raise SystemExit(main())
