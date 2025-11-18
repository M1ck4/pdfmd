"""pdfmd package initializer.

Exposes the core high-level API:

    from pdfmd import pdf_to_markdown, Options

This API is stable and automatically benefits from engine improvements
such as:

    - OCR-aware extraction (native / auto / Tesseract / OCRmyPDF)
    - Table detection and Markdown table rendering
    - Math-aware normalization and preservation (LaTeX-style)

Also provides __version__ and a console entry hint so that:

    python -m pdfmd

behaves like invoking the CLI directly.
"""
from __future__ import annotations

from .models import Options
from .pipeline import pdf_to_markdown

__all__ = ["Options", "pdf_to_markdown"]

# Semantic version for the "Tables & Math" minor release.
__version__ = "1.5.0"


def main() -> None:
    """Entry point alias for `python -m pdfmd.cli`.

    This allows `python -m pdfmd` to behave like running the CLI directly.
    """
    from .cli import main as _main
    raise SystemExit(_main())


if __name__ == "__main__":
    main()
