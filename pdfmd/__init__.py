"""pdfmd package initializer.

Exposes the core high-level API:

    from pdfmd import pdf_to_markdown, Options

This API is stable and automatically benefits from engine improvements
such as:

    - OCR-aware extraction (native / auto / Tesseract / OCRmyPDF)
    - Configurable OCR language (single or combined, e.g. 'eng+fra')
    - Table detection and Markdown table rendering
    - Math-aware normalization and preservation (LaTeX-style)
    - Multi-file batch conversion with safe output naming
    - Robust image export with automatic colorspace conversion

Also provides __version__ and a console entry hint so that:

    python -m pdfmd

behaves like invoking the CLI directly.
"""
from __future__ import annotations

from .models import Options
from .pipeline import pdf_to_markdown

__all__ = ["Options", "pdf_to_markdown", "__version__"]

# v1.6.0: OCR language selection, multi-file batch GUI, GUI redesign,
#          colorspace fix for image export, init-order crash fix.
__version__ = "1.6.0"


def main() -> None:
    """Entry point alias for `python -m pdfmd.cli`.

    This allows `python -m pdfmd` to behave like running the CLI directly.
    """
    from .cli import main as cli_main
    raise SystemExit(cli_main())


if __name__ == "__main__":
    main()