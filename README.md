# PDF to Markdown Converter (Obsidian-Ready)

*A cross-platform **offline** desktop and CLI application for converting PDFs, including scanned documents, into beautifully formatted Markdown optimized for Obsidian and other knowledge tools.*

**Built for simplicity. Enhanced with intelligence.**

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Version](https://img.shields.io/badge/version-v1.1.0-blue)
[![Download EXE](https://img.shields.io/badge/Download-Windows%20EXE-brightgreen)](https://github.com/M1ck4/pdf_to_md/releases/latest/download/PDF_to_MD.exe)

---

## 🕵️ Privacy & Security

**PDF to Markdown Converter is 100% offline.**
No internet connection is ever required, and **no data is uploaded or tracked**.
All processing happens **locally on your computer**, meaning your PDFs, research papers, and notes never leave your device.

> This ensures full privacy and peace of mind for academics, professionals, and anyone handling sensitive material.

---

## 🖼️ Screenshot

![PDF to Markdown Converter Interface](doc/Screenshot%202025-11-11%20173246.png)

---

## ✨ Highlights

* ✅ Converts both text-based and scanned PDFs to Markdown
* 🧠 AI-style text reconstruction - smart heading detection & paragraph logic (now with inline hyphen unwrap + callouts during rendering)
* ⚙️ Modular design for maintainability and future expansion
* 🧩 OCR via **Tesseract** (Windows) or **OCRmyPDF** (macOS/Linux)
* 💡 Configurable from GUI or CLI
* 🔄 Cross-platform and **completely offline**
* 🔐 No tracking, no telemetry, no uploads

---

## 🧠 Architecture Overview

| Module             | Purpose                                                                         |
| ------------------ | ------------------------------------------------------------------------------- |
| **`extract.py`**   | Extracts text and images from PDFs using PyMuPDF and OCR integrations.          |
| **`transform.py`** | Cleans, normalizes, and reconstructs text (handles hyphens, orphans, headings). |
| **`render.py`**    | Converts processed text into Markdown with formatting and image references.     |
| **`pipeline.py`**  | Coordinates the extraction, transformation, and rendering steps.                |
| **`utils.py`**     | Provides cross-platform helpers, logging, and formatting utilities.             |
| **`models.py`**    | Defines data structures and configuration models (Options, Document, Page).     |
| **`app_gui.py`**   | Tkinter-based graphical interface with live progress and error recovery.        |
| **`cli.py`**       | Command-line interface for batch and scripted use cases.                        |

> 🔍 **Design philosophy:** Each component handles one responsibility cleanly — enabling easy debugging, testing, and feature addition.

---

## 🧩 OCR Design: Intelligent Dual-Engine System

This project uses an adaptive OCR engine strategy designed for **maximum reliability across operating systems**.

| Platform       | Default OCR Engine | Description                                                                                 |
| -------------- | ------------------ | ------------------------------------------------------------------------------------------- |
| 🦩 **Windows** | **Tesseract**      | Fast, lightweight, and reliable on Windows — perfect for single-page or embedded-text PDFs. |
| 🍎 **macOS**   | **OCRmyPDF**       | Provides layout-accurate, multi-core OCR — ideal for full-document scans.                   |
| 🐧 **Linux**   | **OCRmyPDF**       | Native and stable; best choice for batch OCR and automation.                                |

**How it works:**

* When OCR mode is set to `auto`, the program detects your OS and chooses the best engine.
* Windows defaults to **Tesseract** for reliability.
* macOS and Linux default to **OCRmyPDF** when available.
* If OCRmyPDF isn’t installed, it falls back automatically to Tesseract.

This ensures smooth, predictable OCR performance regardless of platform.

---

## ⚙️ Installation

### 🐍 Python Setup (All OS)

```bash
pip install pymupdf pillow pytesseract ocrmypdf
git clone https://github.com/M1ck4/pdf_to_md.git
cd pdf_to_md
python app_gui.py
```

### 💻 Windows Executable

```text
Download PDF_to_MD.exe → Double-click → Convert.
```

No Python needed.

---

## 📘 OCR Requirements

| Engine        | Type          | Platform            | Notes                                                     |
| ------------- | ------------- | ------------------- | --------------------------------------------------------- |
| **Tesseract** | Local         | Windows/macOS/Linux | Lightweight, fast, great for single-page or embedded text |
| **OCRmyPDF**  | System/Python | Linux/macOS/WSL     | Handles full layout and multi-page structure              |

> ⚠️ **Windows users:**
> If Tesseract isn’t found, install it from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
> and ensure it’s added to PATH.
> Default path example:
> `C:\Program Files\Tesseract-OCR\tesseract.exe`

---

## 🚀 Usage

### GUI Mode

```bash
python app_gui.py
```

or launch `PDF_to_MD.exe`

### CLI Mode

```bash
python cli.py input.pdf --ocr auto --export-images
```

| Option                      | Description                                |           |            |                   |
| --------------------------- | ------------------------------------------ | --------- | ---------- | ----------------- |
| `--ocr [off                 | auto                                       | tesseract | ocrmypdf]` | Select OCR engine |
| `--preview`                 | Convert first 3 pages only                 |           |            |                   |
| `--export-images`           | Extract images to `_assets/`               |           |            |                   |
| `--insert-page-breaks`      | Add `---` between pages                    |           |            |                   |
| `--remove-headers`          | Remove repeating headers/footers           |           |            |                   |
| `--heading-size-ratio 1.15` | Font-size multiplier for heading detection |           |            |                   |
| `--orphan-max-len 45`       | Maximum characters for orphan merging      |           |            |                   |
| `--aggressive-hyphen`       | Merge TitleCase hyphenation aggressively   |           |            |                   |
| `--no-protect-code-blocks`  | Allow unwrap/reflow inside fenced code     |           |            |                   |

---

## 🗂️ Example Output

**Input PDF:**

```
CHAPTER 1: INTRODUCTION
This is a paragraph that wraps across
multiple lines in the PDF file.
• First bullet point
• Second bullet point
```

**Output Markdown:**

```markdown
# CHAPTER 1: INTRODUCTION

This is a paragraph that wraps across multiple lines in the PDF file.

- First bullet point
- Second bullet point
```

---

## 🦯 Performance Tips

* For **large PDFs**, use `--preview` first to test formatting.
* On slower systems, lower OCR DPI:

  ```python
  opts.ocr_dpi = 200
  ```
* Disable OCR entirely for text-based PDFs to maximize speed.

---

## 🤰🏻 Building the EXE

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name PDF_to_MD --paths . --collect-all pymupdf --collect-all PIL app_gui.py
```

Output: `dist/PDF_to_MD.exe`

---

## 🤓 Troubleshooting

| Issue                         | Cause                        | Fix                                                |
| ----------------------------- | ---------------------------- | -------------------------------------------------- |
| **OCR not working**           | Tesseract not in PATH        | Install and add to PATH, or specify in `Options()` |
| **CLI “ModuleNotFoundError”** | Running from wrong directory | Run from parent folder (`python cli.py`)           |
| **Weird characters**          | Font encoding issues         | Try OCRmyPDF mode                                  |
| **Crashes mid-way**           | Memory limits on large PDFs  | Use `--preview` or lower DPI                       |

---

## 🤝 Contributing

You can help by:

* Reporting issues and submitting sample PDFs.
* Improving OCR heuristics or Markdown formatting.
* Expanding multi-language OCR support.

### Developer Setup

```bash
git clone https://github.com/M1ck4/pdf_to_md.git
cd pdf_to_md
pip install -r requirements.txt
python app_gui.py
pip install -r requirements.txt
python pdf_to_md.py
```

---

## 📾 License

Licensed under the MIT License.
See [LICENSE](LICENSE).

---
## License
Released under the MIT License. See [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

* [PyMuPDF](https://pymupdf.readthedocs.io/)
* [Pillow](https://python-pillow.org/)
* [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
* [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF)
* [Obsidian](https://obsidian.md/)

---

## ❤️ Made for creators, researchers, and readers.

**Free. Open. Useful. Private. Forever.**
## Acknowledgements
- Built on top of [PyMuPDF](https://pymupdf.readthedocs.io/).
- Designed for seamless import into [Obsidian](https://obsidian.md/) vaults and other Markdown-first workflows.
