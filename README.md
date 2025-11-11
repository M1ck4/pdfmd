# PDF to Markdown Converter (Obsidian-Ready)

*A cross-platform desktop and CLI application for converting PDFs — including scanned documents — into beautifully formatted Markdown optimized for Obsidian and other knowledge tools.*

**Built for simplicity. Enhanced with intelligence.**

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Version](https://img.shields.io/badge/version-v2.0.0-blue)
[![Download EXE](https://img.shields.io/badge/Download-Windows%20EXE-brightgreen)](https://github.com/M1ck4/pdf_to_md/releases/latest/download/PDF_to_MD.exe)

---

## 🖼️ Screenshot

![PDF to Markdown Converter Interface](doc/Screenshot%202025-11-11%20173246.png)

---

## ✨ Highlights

* ✅ Converts both text-based and scanned PDFs to Markdown
* 🧠 AI-style text reconstruction — smart heading detection & paragraph logic
* ⚙️ Modular design for maintainability and future expansion
* 🧩 OCR via Tesseract or OCRmyPDF
* 💡 Configurable from GUI or CLI
* 🔄 Cross-platform support: Windows, macOS, Linux

---

## 🧠 Architecture Overview

This project is built around a modular pipeline:

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

## 🧩 OCR Engine Flow

```
              ┌────────────────────────┐
              │        Input PDF       │
              └────────────┬───────────┘
                           │
                    Text-based? ───▶ Yes ───▶ Extract via PyMuPDF
                           │
                           ▼
                          No
                           │
                           ▼
             ┌────────────────────────────┐
             │ OCR Engine (auto-detect)   │
             │                            │
             │ - Tesseract (fast, local)  │
             │ - OCRmyPDF (full layout)   │
             └────────────────────────────┘
                           │
                           ▼
                   Clean & Format → Markdown
```

---

## ⚙️ Installation

### 🐍 Python Setup (All OS)

```bash
pip install pymupdf pillow pytesseract ocrmypdf
git clone https://github.com/M1ck4/pdf_to_md.git
cd pdf_to_md
python -m pdfmd.app_gui
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

> ⚠️ Windows users: If Tesseract isn’t found, install it from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) and ensure it’s on PATH.

---

## 🚀 Usage

### GUI Mode

```bash
python -m pdfmd.app_gui
```

or
launch `PDF_to_MD.exe`

### CLI Mode

```bash
python -m pdfmd.cli input.pdf --ocr auto --export-images
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

---

## 📂 Example Output

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

## 🧭 Performance Tips

* For **large PDFs**, use `--preview` first to test formatting.
* On slower systems, lower OCR DPI:

  ```python
  opts.ocr_dpi = 200
  ```
* Disable OCR entirely for text-based PDFs to maximize speed.

---

## 🧰 Building the EXE

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name PDF_to_MD --paths . --collect-all pymupdf --collect-all PIL pdfmd/app_gui.py
```

Output: `dist/PDF_to_MD.exe`

---

## 🧠 Troubleshooting

| Issue                         | Cause                        | Fix                                                |
| ----------------------------- | ---------------------------- | -------------------------------------------------- |
| **OCR not working**           | Tesseract not in PATH        | Install and add to PATH, or specify in `Options()` |
| **CLI “ModuleNotFoundError”** | Running from wrong directory | Run from parent folder (`python -m pdfmd.cli`)     |
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
python -m pdfmd.app_gui
```

---

## 🧾 License

Licensed under the MIT License.
See [LICENSE](LICENSE).

---

## 🙏 Acknowledgments

* [PyMuPDF](https://pymupdf.readthedocs.io/)
* [Pillow](https://python-pillow.org/)
* [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
* [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF)
* [Obsidian](https://obsidian.md/)

---

## ❤️ Made for creators, researchers, and readers.

**Free. Open. Useful. Forever.**
