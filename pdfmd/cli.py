"""Command-line interface for pdfmd.

Fast, local, privacy-first PDF → Markdown converter with table and math-aware
conversion (LaTeX-style equations, Unicode math, and text tables rendered as
Markdown).

Usage (basic):

  pdfmd input.pdf
  pdfmd input.pdf -o notes.md
  pdfmd *.pdf --ocr auto --stats

All processing happens locally. No uploads, no telemetry, no tracking.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from .models import Options
from .pipeline import pdf_to_markdown


# ---------------------------------------------------------------------------
# Colour handling
# ---------------------------------------------------------------------------


@dataclass
class _Colors:
    ok: str
    warn: str
    err: str
    info: str
    reset: str


def _make_colors(enable: bool) -> _Colors:
    if not enable:
        return _Colors("", "", "", "", "")
    return _Colors(
        ok="\033[32m",      # green
        warn="\033[33m",    # yellow
        err="\033[31m",     # red
        info="\033[36m",    # cyan
        reset="\033[0m",
    )


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    description = (
        "Convert PDF files to clean, Obsidian-ready Markdown with table and "
        "math-aware conversion.\n"
        "Runs fully offline: no uploads, no telemetry, no cloud dependencies."
    )

    epilog = r"""
Examples:

  # Basic conversion (writes input.md next to the PDF)
  pdfmd report.pdf

  # Choose an explicit output file
  pdfmd report.pdf -o report_notes.md

  # Auto-detect scanned pages and OCR as needed
  pdfmd scan.pdf --ocr auto

  # Force Tesseract OCR and export page images
  pdfmd book_scan.pdf --ocr tesseract --export-images

  # Preview only (first few pages) with stats
  pdfmd long_paper.pdf --preview-only --stats

  # Batch convert multiple PDFs into a folder
  pdfmd *.pdf --ocr auto -o out_md/

  # Quiet mode, non-interactive (good for scripts)
  pdfmd confidential.pdf --ocr auto --no-progress --quiet

Tables and math:

  • Text tables are detected and rendered as GitHub-flavoured Markdown tables.
  • Common Unicode math, Greek letters, subscripts and superscripts are
    normalised to LaTeX-style math so expressions like E = mc², x₁₀², α + β³
    survive the round-trip as equations instead of broken text.
  • LaTeX-like math already present in the PDF is preserved and not escaped
    as normal Markdown text.

Security notes:

  • All processing happens on your machine.
  • Passwords are read interactively (no echo), never logged,
    and never sent to other processes via command-line arguments.
  • Output Markdown files are written unencrypted; protect them
    according to your environment's security requirements.
"""

    parser = argparse.ArgumentParser(
        prog="pdfmd",
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "inputs",
        metavar="INPUT_PDF",
        nargs="*",  # CHANGED: '*' allows zero inputs (was '+')
        help="Path(s) to input PDF file(s). You can pass multiple PDFs.",
    )

    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT",
        help=(
            "Output path. For a single input this is an .md file.\n"
            "For multiple inputs this is treated as an output directory."
        ),
    )

    parser.add_argument(
        "--ocr",
        choices=["off", "auto", "tesseract", "ocrmypdf"],
        default="off",
        help=(
            "OCR mode (default: off):\n"
            "  off        — use native text only\n"
            "  auto       — detect scanned pages and OCR as needed\n"
            "  tesseract  — force page-by-page Tesseract OCR\n"
            "  ocrmypdf   — use OCRmyPDF for high-fidelity layout"
        ),
    )

    parser.add_argument(
        "--export-images",
        action="store_true",
        help="Export images to an _assets/ folder and append Markdown references.",
    )

    parser.add_argument(
        "--page-breaks",
        action="store_true",
        help="Insert '---' page break markers between pages in the output.",
    )

    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Only process the first few pages (useful for quick inspection).",
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable the terminal progress bar.",
    )

    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress non-error messages; only show errors.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity. Use -v for more logs, -vv for debug-level detail.",
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help=(
            "After conversion, print basic stats "
            "(words, headings, tables, lists)."
        ),
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable coloured output.",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit.",
    )

    return parser


# ---------------------------------------------------------------------------
# Options helper
# ---------------------------------------------------------------------------


def _make_options(args: argparse.Namespace) -> Options:
    opts = Options()

    # Extraction / OCR
    opts.ocr_mode = args.ocr
    opts.preview_only = bool(args.preview_only)

    # Rendering / output
    opts.insert_page_breaks = bool(args.page_breaks)
    opts.export_images = bool(args.export_images)

    # Transform heuristics remain at their defaults; they can be exposed later.
    return opts


# ---------------------------------------------------------------------------
# Progress bar with ETA
# ---------------------------------------------------------------------------


def _make_progress_cb(
    file_label: str,
    colors: _Colors,
    args: argparse.Namespace,
) -> Callable[[int, int], None]:
    start = time.time()

    def progress_cb(done: int, total: int) -> None:
        if args.no_progress or args.quiet:
            return

        # In the pipeline, progress_cb is called with (pct, 100) where pct is 0—100.
        if total == 100 and 0 <= done <= 100:
            pct = int(done)
        else:
            pct = int(done * 100 / total) if total > 0 else 0
        pct = max(0, min(100, pct))

        elapsed = time.time() - start
        eta_str = "ETA: --"
        if pct > 0 and elapsed > 0:
            remaining = elapsed * (100 - pct) / pct
            if remaining < 90:
                eta_str = f"ETA: {int(remaining)}s"
            else:
                eta_str = f"ETA: {int(remaining // 60)}m"

        bar_width = 24
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "░" * (bar_width - filled)

        line = f"\r{colors.info}[{bar}] {pct:3d}% {eta_str}  {file_label}{colors.reset}"
        sys.stderr.write(line)
        sys.stderr.flush()

        if pct >= 100:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return progress_cb


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


@dataclass
class ConversionStats:
    words: int
    headings: int
    tables: int
    lists: int


def _is_table_header_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and len(s) > 3


def _is_table_sep_line(line: str) -> bool:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return False
    inner = s.strip("|").replace("-", "").replace(":", "").strip()
    return inner == ""


def _compute_stats(md_path: Path) -> ConversionStats:
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return ConversionStats(words=0, headings=0, tables=0, lists=0)

    lines = text.splitlines()

    # Simple word count
    words = len(re.findall(r"\w+", text))

    # Headings: lines starting with '#'
    headings = sum(1 for ln in lines if ln.lstrip().startswith("#"))

    # Lists: lines starting with -, *, +
    lists = sum(
        1
        for ln in lines
        if ln.lstrip().startswith("- ")
        or ln.lstrip().startswith("* ")
        or ln.lstrip().startswith("+ ")
    )

    # Tables: header + separator pairs
    tables = 0
    i = 0
    n = len(lines)
    while i < n - 1:
        if _is_table_header_line(lines[i]) and _is_table_sep_line(lines[i + 1]):
            tables += 1
            i += 2
            while i < n and lines[i].strip().startswith("|"):
                i += 1
            continue
        i += 1

    return ConversionStats(words=words, headings=headings, tables=tables, lists=lists)


def _print_stats(path: Path, stats: ConversionStats, colors: _Colors) -> None:
    sys.stderr.write(
        f"{colors.info}Stats for {path.name}:{colors.reset}\n"
        f"  Words:     {stats.words}\n"
        f"  Headings:  {stats.headings}\n"
        f"  Tables:    {stats.tables}\n"
        f"  Lists:     {stats.lists}\n"
    )
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Core conversion for a single file
# ---------------------------------------------------------------------------


def _run_single(
    inp: Path,
    outp: Path,
    opts: Options,
    args: argparse.Namespace,
    colors: _Colors,
) -> bool:
    """Run conversion for one input/output pair.

    Returns True on success, False on failure.
    """
    if not inp.is_file():
        if not args.quiet:
            sys.stderr.write(
                f"{colors.err}Error:{colors.reset} input file not found: {inp}\n"
            )
        return False

    if not args.quiet:
        sys.stderr.write(
            f"{colors.info}Converting{colors.reset} {inp} "
            f"→ {colors.ok}{outp}{colors.reset}\n"
        )
        sys.stderr.flush()

    # Decide logging callback based on verbosity / quiet
    if args.quiet:
        log_cb: Optional[Callable[[str], None]] = None
    elif args.verbose >= 1:
        def log_cb(msg: str) -> None:
            sys.stderr.write(f"{colors.info}{msg}{colors.reset}\n")
    else:
        log_cb = None

    progress_cb = _make_progress_cb(inp.name, colors, args)

    password: Optional[str] = None  # kept local, never persisted

    def run_once(pdf_password: Optional[str]) -> None:
        pdf_to_markdown(
            str(inp),
            str(outp),
            opts,
            progress_cb=progress_cb,
            log_cb=log_cb,
            pdf_password=pdf_password,
        )

    try:
        # First attempt with no password (or whatever we have)
        run_once(password)
        return True

    except Exception as exc:
        # Look for password / encryption related errors
        lower = str(exc).lower()
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
            if not args.quiet:
                sys.stderr.write(f"{colors.err}Error:{colors.reset} {exc}\n")
                if args.verbose >= 2:
                    traceback.print_exc(file=sys.stderr)
            return False

        # Encrypted PDF, interactive password prompt required.
        if not sys.stdin.isatty():
            if not args.quiet:
                sys.stderr.write(
                    f"{colors.err}Error:{colors.reset} "
                    "PDF is password protected and interactive input is not available.\n"
                )
            return False

        try:
            password = getpass.getpass(
                "PDF is password protected. Enter password (input will be hidden): "
            )
        except Exception as e_input:
            if not args.quiet:
                sys.stderr.write(
                    f"{colors.err}Error reading password:{colors.reset} {e_input}\n"
                )
            return False

        if not password:
            if not args.quiet:
                sys.stderr.write(
                    f"{colors.warn}No password provided; skipping file.{colors.reset}\n"
                )
            return False

        try:
            run_once(password)
            return True
        except Exception as exc2:
            if not args.quiet:
                sys.stderr.write(
                    f"{colors.err}Error after password attempt:{colors.reset} {exc2}\n"
                )
                if args.verbose >= 2:
                    traceback.print_exc(file=sys.stderr)
            return False

    finally:
        # Best-effort hygiene: drop any reference to the password.
        password = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Version info - CHECK THIS FIRST before requiring inputs
    try:
        from . import __version__ as _VERSION
    except Exception:
        _VERSION = "unknown"

    if args.version:
        print(f"pdfmd {_VERSION}")
        return 0

    # NOW check if inputs were provided
    if not args.inputs:
        parser.print_help()
        return 1

    # Colour configuration
    enable_color = sys.stderr.isatty() and not args.no_color
    colors = _make_colors(enable_color)

    if args.quiet:
        # Quiet suppresses verbosity
        args.verbose = 0

    opts = _make_options(args)

    # Prepare inputs
    inputs: List[Path] = [Path(p).expanduser() for p in args.inputs]

    # Interpret output argument
    out_arg = Path(args.output).expanduser() if args.output else None
    multiple = len(inputs) > 1

    if multiple and out_arg is not None and out_arg.exists() and not out_arg.is_dir():
        sys.stderr.write(
            f"{colors.err}Error:{colors.reset} when converting multiple inputs, "
            f"--output must be a directory.\n"
        )
        return 1

    if multiple and out_arg is not None and not out_arg.exists():
        try:
            out_arg.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            sys.stderr.write(
                f"{colors.err}Error creating output directory:{colors.reset} {exc}\n"
            )
            return 1

    successes = 0
    failures = 0

    for inp in inputs:
        if not inp.is_file():
            if not args.quiet:
                sys.stderr.write(
                    f"{colors.err}Error:{colors.reset} input file not found: {inp}\n"
                )
            failures += 1
            continue

        if out_arg is None:
            outp = inp.with_suffix(".md")
        else:
            if multiple or out_arg.is_dir():
                outp = out_arg / (inp.stem + ".md")
            else:
                outp = out_arg

        ok = _run_single(inp, outp, opts, args, colors)

        if ok:
            successes += 1
            if args.stats:
                stats = _compute_stats(outp)
                _print_stats(outp, stats, colors)
        else:
            failures += 1

    if not args.quiet:
        if failures == 0:
            sys.stderr.write(
                f"{colors.ok}Done.{colors.reset} "
                f"{successes} file(s) converted successfully.\n"
            )
        else:
            sys.stderr.write(
                f"{colors.err}Finished with errors.{colors.reset} "
                f"{successes} succeeded, {failures} failed.\n"
            )
        sys.stderr.flush()

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
