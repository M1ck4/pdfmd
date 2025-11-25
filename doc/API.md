# `pdfmd` Python API

This document describes the **Python library interface** for `pdfmd` – for developers who want to call the converter from their own code, integrate it into automation, or build their own tools on top of the core engine.

For installation, CLI usage, and GUI details, see `README.md`.
This file focuses on **programmatic use**.

---

## 1. Public API Overview

The **official public API** is intentionally small and lives at the top-level package:

```python
from pdfmd import pdf_to_markdown, Options, __version__
```

* `pdf_to_markdown(...)` – high-level, end‑to‑end conversion (PDF → Markdown file)
* `Options` – configuration object controlling OCR, layout, and output behaviour
* `__version__` – current package version string

Everything else is available via submodules (e.g. `pdfmd.extract`, `pdfmd.tables`), but those are considered **advanced / semi‑internal** and may evolve more quickly.

---

## 2. Quick Start

### 2.1 Basic Conversion

```python
from pdfmd import pdf_to_markdown, Options

opts = Options()

pdf_to_markdown(
    input_pdf="paper.pdf",
    output_md="paper.md",
    options=opts,
)
```

This is equivalent to running:

```bash
pdfmd paper.pdf
```

### 2.2 With OCR, Images, and Page Breaks

```python
from pdfmd import pdf_to_markdown, Options

opts = Options(
    ocr_mode="auto",         # "off" | "auto" | "tesseract" | "ocrmypdf"
    export_images=True,
    insert_page_breaks=True,
)

pdf_to_markdown(
    input_pdf="scan.pdf",
    output_md="scan.md",
    options=opts,
)
```

### 2.3 Integrating With a Progress Bar

```python
from pdfmd import pdf_to_markdown, Options


def progress_cb(done, total):
    pct = 100 * done / max(total, 1)
    print(f"\rConverting… {pct:5.1f}%", end="", flush=True)


opts = Options(ocr_mode="auto")

pdf_to_markdown(
    input_pdf="big_report.pdf",
    output_md="big_report.md",
    options=opts,
    progress_cb=progress_cb,
)

print("\nDone.")
```

---

## 3. High‑Level Conversion API

### 3.1 `pdf_to_markdown`

```python
from pdfmd import pdf_to_markdown, Options

pdf_to_markdown(
    input_pdf: str,
    output_md: str,
    options: Options,
    progress_cb: callable | None = None,
    log_cb: callable | None = None,
    pdf_password: str | None = None,
    debug_tables: bool = False,
) -> None
```

**Description**
Run the full `pdfmd` pipeline:

1. **Extract** – text, layout blocks, and images from the PDF (optionally with OCR)
2. **Transform** – clean text, remove headers/footers, detect headings/lists, prepare tables & math
3. **Render** – generate Markdown and write `output_md`, optionally exporting images

#### Parameters

* `input_pdf: str`
  Path to the input PDF file. Must point to an existing file ending in `.pdf`.

* `output_md: str`
  Path where the Markdown output will be written. Parent directories must exist.

* `options: Options`
  Configuration object controlling OCR, headings, image export, preview mode, and structural heuristics. See [Options](#4-options-dataclass) below.

* `progress_cb: callable | None = None`
  Optional callback for progress reporting.

  ```python
  def progress_cb(done_pages: int, total_pages: int) -> None:
      ...
  ```

  Called periodically as pages are processed. Useful for terminal progress bars, GUI progress indicators, or logging.

* `log_cb: callable | None = None`
  Optional logging hook.

  ```python
  def log_cb(message: str) -> None:
      ...
  ```

  If provided, the pipeline will send human‑readable messages such as:

  * `"[pipeline] Extracting text…"`
  * `"[pipeline] Removed repeating edges…"`
  * `"[tables] detected 3 table blocks"`

  If `log_cb` is `None`, the CLI/GUI provide their own default logging. In library usage you can redirect logs to your own logger.

* `pdf_password: str | None = None`
  Optional password for encrypted PDFs. If the file is encrypted and `pdf_password` is not provided, the caller is responsible for prompting the user and retrying with the correct password.

* `debug_tables: bool = False`
  Enable additional debug logging from the table detection module. Intended for developers trying to tune behaviour on tricky documents.

#### Return Value

* Returns `None`.
* On success, `output_md` is written to disk (and image assets if `options.export_images=True`).

#### Exceptions

Typical exceptions you may want to catch:

* `FileNotFoundError` – `input_pdf` does not exist
* `ValueError` – invalid options (e.g. unsupported OCR mode)
* `RuntimeError` – missing required dependencies (PyMuPDF, Tesseract, OCRmyPDF, etc.)
* Any other error raised while parsing or rendering a specific PDF

In automation scripts you might do:

```python
try:
    pdf_to_markdown(...)
except FileNotFoundError as e:
    # handle missing input
    ...
except RuntimeError as e:
    # usually missing dependencies or fatal OCR issues
    ...
```

---

## 4. `Options` dataclass

`Options` encapsulates all tunable behaviours for the conversion pipeline.

```python
from pdfmd import Options

opts = Options()
```

You can either construct with defaults and override fields:

```python
opts = Options()
opts.ocr_mode = "auto"
opts.export_images = True
opts.insert_page_breaks = True
```

or set them during construction:

```python
opts = Options(
    ocr_mode="ocrmypdf",
    preview_only=False,
    caps_to_headings=True,
    defragment_short=True,
    heading_size_ratio=1.15,
    orphan_max_len=45,
    remove_headers_footers=True,
    insert_page_breaks=False,
    export_images=False,
)
```

### 4.1 Fields

Below is an overview of the key configuration fields. Defaults are taken from `Options` in `pdfmd.models`.

#### `ocr_mode: Literal["off", "auto", "tesseract", "ocrmypdf"]`

Controls OCR usage:

* `"off"` (default) – no OCR; rely purely on native PDF text extraction.
* `"auto"` – detect scanned pages heuristically; OCR only those.
* `"tesseract"` – run Tesseract OCR on every page.
* `"ocrmypdf"` – pre‑process the PDF with OCRmyPDF for high‑fidelity layout.

Use `auto` for mixed PDFs, `tesseract` or `ocrmypdf` for fully scanned documents.

#### `preview_only: bool`

* If `True`, only the first few pages are processed (e.g. first 3).
  Useful for testing settings on large PDFs before doing a full run.

#### `caps_to_headings: bool`

* If `True` (default), lines that are **ALL CAPS** or **mostly caps** are promoted to headings, using font size and casing heuristics.
* If `False`, headings are inferred primarily via font size alone.

#### `defragment_short: bool`

* If `True` (default), short isolated lines (“orphans”) are merged into surrounding paragraphs when safe.
  This greatly improves readability for narrow‑column PDFs where every second line is broken.

#### `heading_size_ratio: float`

* Default around `1.15`.
* A line is considered a heading if its font size is at least `body_size * heading_size_ratio` (subject to other heuristics).
* Lowering this value produces **more headings**; raising it makes heading detection more conservative.

#### `orphan_max_len: int`

* Maximum character length for a line to be considered an “orphan” candidate for merging.
* Typical default: `45`.

#### `remove_headers_footers: bool`

* If `True` (default), the pipeline detects repeating first/last lines across pages and removes them as headers/footers.
* If `False`, per‑page headers/footers are preserved in the Markdown output.

#### `insert_page_breaks: bool`

* If `True`, insert `---` between pages in the final Markdown.
* Helpful when converting **slides** or **handouts** where page boundaries matter.

#### `export_images: bool`

* If `True`, export images as `PNG` into a sidecar `*_assets/` folder next to the Markdown file, and append Markdown image references.
* If `False` (default), images are ignored.

> **Note:** Additional internal fields may exist on `Options`. The ones listed here are the primary knobs expected to remain stable. Less common/experimental fields may change between versions.

---

## 5. Advanced / Semi‑Internal APIs

If you need more control than `pdf_to_markdown` provides, you can work with the underlying modules directly. These are powerful but less stable – treat them as advanced APIs.

### 5.1 Data Models (`pdfmd.models`)

Core types describing the textual structure extracted from a PDF:

* `Span` – a contiguous run of text with shared font properties (size, weight, italics, font name).
* `Line` – a horizontal grouping of spans.
* `Block` – higher‑level layout unit (paragraphs, table rows, headings, etc.).
* `PageText` – all blocks and images for a single page.
* `Options` – the configuration object described above.

You normally do not construct these manually; they are produced and consumed by the pipeline.

### 5.2 Extraction (`pdfmd.extract`)

Responsible for reading the PDF, optionally performing OCR, and producing structured page content.

Typical usage pattern:

```python
from pdfmd.models import Options
from pdfmd.extract import extract_pages

opts = Options(ocr_mode="auto")

pages = extract_pages(
    input_pdf="paper.pdf",
    options=opts,
    pdf_password=None,
    log_cb=None,
)

# pages is a list of PageText objects
```

This is essentially the **first stage** of `pdf_to_markdown`.

### 5.3 Transformation (`pdfmd.transform`)

Cleans and reshapes page content: removes headers/footers, merges lines, promotes headings, integrates tables and math markers.

```python
from pdfmd.transform import transform_pages

structured_pages = transform_pages(pages, options=opts, log_cb=None)
```

The output preserves layout as a sequence of blocks that will later be rendered to Markdown.

### 5.4 Tables (`pdfmd.tables`)

The table module performs:

* detection of table‑like regions,
* grouping them into rows/columns,
* emission of Markdown table structures.

Most users never call this directly, because `pdf_to_markdown` and `transform_pages` take care of it, but if you want to experiment with custom table handling you can import `tables` and work on the intermediate `PageText`/`Block` structures.

### 5.5 Equations (`pdfmd.equations`)

`equations.py` provides helpers to detect mathematical regions and convert Unicode math (α, β, subscripts, superscripts, ∫, etc.) into LaTeX‑style expressions suitable for `$...$` and `$$...$$` contexts.

In normal use you do not need to touch this; it is wired into the pipeline. But for specialised workflows (e.g. custom markdown dialects or alternative math rendering) you can reuse or adapt its conversion utilities.

### 5.6 Rendering (`pdfmd.render`)

The final stage: convert structured blocks into Markdown text.

```python
from pdfmd.render import render_document

md_text = render_document(structured_pages, options=opts)

with open("output.md", "w", encoding="utf-8") as f:
    f.write(md_text)
```

This is effectively what `pdf_to_markdown` does at the end, after extraction and transformation.

---

## 6. Usage Patterns & Examples

### 6.1 Building Your Own CLI Wrapper

If you want a custom command tailored to your own workflow:

```python
#!/usr/bin/env python3

import argparse
from pdfmd import pdf_to_markdown, Options


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_pdf")
    parser.add_argument("output_md")
    parser.add_argument("--ocr", choices=["off", "auto", "tesseract", "ocrmypdf"], default="off")
    args = parser.parse_args()

    opts = Options(ocr_mode=args.ocr)

    pdf_to_markdown(
        input_pdf=args.input_pdf,
        output_md=args.output_md,
        options=opts,
    )


if __name__ == "__main__":
    main()
```

### 6.2 Integrating With Pandoc

Pipe the Markdown into Pandoc to produce DOCX or HTML:

```python
import subprocess
from pdfmd import pdf_to_markdown, Options

opts = Options(ocr_mode="auto")

pdf_to_markdown("report.pdf", "report.md", options=opts)

subprocess.run([
    "pandoc", "report.md", "-o", "report.docx",
], check=True)
```

### 6.3 Using in Jupyter / Notebooks

```python
from pathlib import Path
from pdfmd import pdf_to_markdown, Options

pdf_path = "paper.pdf"
md_path = Path("paper.md")

opts = Options(ocr_mode="off")

pdf_to_markdown(pdf_path, str(md_path), options=opts)

print(md_path.read_text(encoding="utf-8")[:2000])
```

---

## 7. Versioning & Stability

* The **public API** (`pdf_to_markdown`, `Options`, `__version__`) is intended to remain stable within a major version line.
* Submodule APIs (`extract`, `transform`, `tables`, `equations`, `render`, `models`) are **more flexible** and may gain or refine fields and helpers as the project evolves.

For most integrations, you are encouraged to:

1. Depend primarily on `pdf_to_markdown` + `Options`
2. Use submodules only when you have a clear need for lower‑level access
3. Pin a version in `requirements.txt` if you rely on internal details

---

## 8. Summary

* Use the **CLI** (`pdfmd`) for everyday conversions and scripts.
* Use the **GUI** for interactive, profile‑driven workflows.
* Use this **Python API** when you want to:

  * embed PDF→Markdown conversion in your own tools,
  * build custom automation around OCR, tables, or math,
  * or experiment with the pipeline as a component in a larger system.

The surface area is intentionally small, the behaviours are explicit, and the internals are modular. You get a converter that is not only useful as an app, but also as a **library** you can grow with.
