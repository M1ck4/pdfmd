#!/usr/bin/env python3
"""
Single-file GUI: PDF → Markdown

Enhancements included:
1) Remove repeating headers/footers (auto-detect common first/last lines).
2) Convert URLs to Markdown links.
3) Smarter lists (bullets + numbered; basic lettered handling).
4) Optional page-break markers (---) between pages.
5) Normalize punctuation (quotes, ellipses, en-dash).
6) Detect MOSTLY-CAPS headings (small caps / tracked caps).
7) Ignore drop caps (oversized first letter at paragraph start).
8) Optional image export to an assets/ folder with Markdown links.
9) Determinate per-page progress bar.
10) Persist last-used paths & options in a JSON settings file.
11) Per-page error recovery — if a page fails, skip it and keep going.
12) Preview-only mode (first 3 pages) to test settings on large PDFs.
13) Safer image folder creation (only when images exist) + relative links.

Dependency:
    pip install pymupdf

Tip:
    To make a one-click Windows app:  
    py -m pip install pyinstaller  
    py -m PyInstaller --noconsole --onefile --name "PDF_to_MD" pdf_to_md.py
"""
from __future__ import annotations
import io
import json
import os
import re
import threading
from collections import Counter
from pathlib import Path
from statistics import median
from typing import List, Dict, Any, Tuple

from transform import convert_simple_callouts, two_pass_unwrap

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# -------- Third-party: PyMuPDF --------
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

# ===================== Config persistence =====================
CFG_PATH = Path.home() / ".pdf_to_md_gui.json"

def save_cfg(state: dict):
    try:
        CFG_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass

def load_cfg() -> dict:
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

# ===================== Markdown helpers =====================
MD_SPECIAL = r"\`*_{}[]()#+-.!|>~"
MD_ESCAPE_TABLE = {c: f"\\{c}" for c in MD_SPECIAL}

URL_RE = re.compile(r"(https?://[^\s)]+)")


def md_escape(text: str) -> str:
    """Escape Markdown-reserved punctuation without over-escaping normal prose."""
    return "".join(MD_ESCAPE_TABLE.get(ch, ch) for ch in text)


def linkify(text: str) -> str:
    return URL_RE.sub(lambda m: f"[{m.group(0)}]({m.group(0)})", text)


def normalize_punctuation(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("…", "...").replace("–", "-")
    return s


def wrap_inline(text: str, bold: bool, italic: bool) -> str:
    """Wrap span text with ** and/or * markers for bold/italics."""
    if not text.strip():
        return text
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def span_style(span: Dict[str, Any]) -> Tuple[float, bool, bool, str]:
    """
    Extract (size, is_bold, is_italic, text) from a PyMuPDF span dict.
    Uses flags and font-name hints (robust across PDFs).
    """
    txt = span.get("text", "") or ""
    size = float(span.get("size", 0.0))
    flags = span.get("flags", 0)
    font = (span.get("font", "") or "").lower()

    # Common PyMuPDF bits: italic ~2, bold ~16. Also check font name.
    is_bold = bool(flags & 16) or any(k in font for k in ("bold", "black", "heavy", "semibold"))
    is_italic = bool(flags & 2) or any(k in font for k in ("italic", "oblique"))
    return size, is_bold, is_italic, txt


def is_all_caps_line(s: str) -> bool:
    core = re.sub(r"[^A-Za-z]+", "", s)
    return bool(core) and core.isupper()


def is_mostly_caps(s: str, threshold: float = 0.75) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    if not letters:
        return False
    return sum(1 for ch in letters if ch.isupper()) / len(letters) >= threshold


def fix_hyphenation(text: str) -> str:
    """
    ! Function is disabled: Hypen unwrap is handled in final post-processing (two_pass_unwrap)
    ? See line 443f.: post-processing before `Path(output_md).write_text(md, encoding="utf-8")`
    * Rational: 
    * (1) `fix_hyphenation` is too aggressive, while still being prone to errors when running into alternative hyphenation unicode characters.
    * (2) hyphenation reflow logic has been outsourced to a module to keep ´pdf_to_md.py` tidy.
    """
    # return re.sub(r"-\n(\s*)", r"\1", text)

    return text


def unwrap_hard_breaks(lines: List[str]) -> str:
    """
    ! Function is disabled: smarter linebreak reflow happens before the sink (two_pass_unwrap)
    ? See line 443f.: post-processing before sink `Path(output_md).write_text(md, encoding="utf-8")`
    * Rational: 
    * (1) `unwrap_hard_breaks()` does only preserve paragraphs based on empty lines. New logic checks for common sentence ending punctation in a negative lookbehind and accounts for common abbrevations.
    * (2) reflow logic has been outsourced to a module to keep ´pdf_to_md.py` tidy.
    """
    
    # """Merge wrapped lines into paragraphs. Blank lines remain paragraph breaks."""
    # out, buf = [], []

    # def flush():
    #     if buf:
    #         out.append(" ".join(buf).strip())
    #         buf.clear()

    # for raw in lines:
    #     line = raw.rstrip("\n")
    #     if not line.strip():
    #         flush()
    #         out.append("")
    #         continue
    #     if line.endswith("  "):
    #         buf.append(line.rstrip())
    #         flush()
    #     else:
    #         buf.append(line.strip())

    # flush()
    # return "\n".join(out)
    
    return "\n".join(lines)

def defragment_orphans(md: str, max_len: int = 45) -> str:
    lines = md.splitlines()
    res = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            i > 0
            and i < len(lines) - 1
            and not lines[i - 1].strip()
            and not lines[i + 1].strip()
            and 0 < len(line.strip()) <= max_len
            and not line.strip().startswith("#")
        ):
            j = len(res) - 1
            while j >= 0 and not res[j].strip():
                j -= 1
            if j >= 0:
                res[j] = (res[j].rstrip() + " " + line.strip()).strip()
                i += 2
                continue
        res.append(line)
        i += 1
    return "\n".join(res)


def strip_dropcap(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """If first span is a giant single-letter drop cap, ignore it for size calc/render."""
    if len(spans) >= 2:
        size0, _, _, txt0 = span_style(spans[0])
        size1, _, _, _ = span_style(spans[1])
        if len(txt0.strip()) == 1 and size0 >= size1 * 1.6:
            return spans[1:]
    return spans

# ===================== Header/Footer detection =====================

def detect_repeating_edges(pages_lines: List[List[str]], min_pages: int = 3):
    """Detect a repeating header (first non-blank) and footer (last non-blank) across pages."""
    heads = Counter()
    tails = Counter()
    for lines in pages_lines:
        top = next((ln.strip() for ln in lines if ln.strip()), "")
        bottom = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")
        if top:
            heads[top] += 1
        if bottom:
            tails[bottom] += 1
    header = next((h for h, c in heads.most_common() if c >= min_pages and h), None)
    footer = next((t for t, c in tails.most_common() if c >= min_pages and t), None)
    return header, footer

#* NOTE: Header and Footers (as well as common Watermarks, e.g. in TTRPG books) could also be handled by ignoring the regions all together. Might be worth an option. 

# ===================== save markdown wrapper =====================

#! NOT IN USE: 
#TODO There are more tiding things that could be added to this wrapper. I leave it for later considerations.

# def _save_markdown(output_path: str, md_text: str, *, aggressive_hyphen: bool = False) -> None:
#     """Persist Markdown after a final cleanup pass."""
#     md_text = two_pass_unwrap(md_text, aggressive_hyphen=aggressive_hyphen)
#     md_text = convert_simple_callouts(md_text)
#     Path(output_path).write_text(md_text, encoding="utf-8")


# ===================== PDF → Markdown (page-wise) =====================

def block_to_markdown_lines(
    block: Dict[str, Any],
    body_size: float,
    caps_to_headings: bool,
    heading_size_ratio: float = 1.15,
    linkify_urls: bool = True,
) -> List[str]:
    """Convert one text block to Markdown lines with heading heuristics and bullets."""
    rendered_lines: List[str] = []
    line_sizes: List[float] = []

    for line in block.get("lines", []):
        spans = line.get("spans", [])
        spans = strip_dropcap(spans)
        span_texts, span_sizes = [], []
        for span in spans:
            size, is_bold, is_italic, txt = span_style(span)
            if not txt:
                continue
            t = wrap_inline(md_escape(txt), is_bold, is_italic)
            span_texts.append(t)
            span_sizes.append(size)
        joined = "".join(span_texts)
        if joined.strip():
            rendered_lines.append(joined)
            if span_sizes:
                line_sizes.append(median(span_sizes))

    if not rendered_lines:
        return []

    avg_line_size = median(line_sizes) if line_sizes else body_size
    block_text = "\n".join(rendered_lines).strip()

    # Heading heuristics (size + caps)
    heading_by_size = avg_line_size >= body_size * heading_size_ratio
    heading_by_caps = caps_to_headings and (is_all_caps_line(block_text.replace("\n", " ")) or is_mostly_caps(block_text))

    if heading_by_size or heading_by_caps:
        level = 1 if (avg_line_size >= body_size * 1.6) or heading_by_caps else 2
        single = re.sub(r"\s+", " ", block_text).strip(" -:–—")
        single = normalize_punctuation(single)
        return [f"{'#' * level} {single}", ""]

    # Paragraph(s)
    para_text = fix_hyphenation("\n".join(rendered_lines))

    lines: List[str] = []
    for ln in para_text.splitlines():
        # bullets
        if re.match(r"^\s*([•○◦·\-–—])\s+", ln):
            ln = re.sub(r"^\s*[•○◦·–—-]\s+", "- ", ln)
            lines.append(ln.strip())
            continue
        # numbered lists (1.  2)  etc.)
        m_num = re.match(r"^\s*(\d+)[\.)]\s+", ln)
        if m_num:
            num = m_num.group(1)
            ln = re.sub(r"^\s*\d+[\.)]\s+", f"{num}. ", ln)
            lines.append(ln.strip())
            continue
        # lettered outlines (a) b. → simple bullet)
        if re.match(r"^\s*[A-Za-z][\.)]\s+", ln):
            ln = re.sub(r"^\s*[A-Za-z][\.)]\s+", "- ", ln)
            lines.append(ln.strip())
            continue
        lines.append(ln)

    para = unwrap_hard_breaks(lines)
    para = normalize_punctuation(para)
    if linkify_urls:
        para = linkify(para)
    return [para, ""]


def pdf_to_markdown(
    input_pdf: str,
    output_md: str,
    caps_headings: bool = True,
    defragment_short: bool = True,
    heading_size_ratio: float = 1.15,
    orphan_max_len: int = 45,
    insert_page_breaks: bool = False,
    remove_headers_footers: bool = True,
    export_images: bool = False,
    preview_only: bool = False,
    progress_cb=None,
) -> None:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not installed. Install with: pip install pymupdf")

    doc = fitz.open(input_pdf)
    total_pages = doc.page_count
    pages_to_process = 3 if preview_only and total_pages > 3 else total_pages

    # Pass 1: collect page text lines for header/footer detection
    pages_lines: List[List[str]] = []
    for pno in range(pages_to_process):
        page = doc.load_page(pno)
        #? UNTESTED Little change here: Shouldn't this fix a lot of column recognition issues? (also see line 327)
        # raw_text = page.get_text("text")
        raw_text = page.get_text("text", sort=True) 
        pages_lines.append(raw_text.splitlines())

    header, footer = (None, None)
    if remove_headers_footers:
        header, footer = detect_repeating_edges(pages_lines, min_pages=3)

    # Prepare image export folder lazily (only if images found)
    assets_dir: Path | None = None

    md_lines: List[str] = []
    for pno in range(pages_to_process):
        try:
            page = doc.load_page(pno)
            #? UNTESTED Little change here: Shouldn't this fix a lot of column recognition issues? (also see line 311)
            # info = page.get_text("dict")
            info = page.get_text("dict", sort=True)

            # build blocks, skipping header/footer lines when possible
            page_blocks = []
            for b in info.get("blocks", []):
                if "lines" not in b:
                    continue
                new_lines = []
                for line in b.get("lines", []):
                    span_texts = [span.get("text", "") for span in line.get("spans", [])]
                    joined = "".join(span_texts).strip()
                    if not joined:
                        continue
                    if remove_headers_footers:
                        if header and joined.strip() == header:
                            continue
                        if footer and joined.strip() == footer:
                            continue
                    new_lines.append({"spans": line.get("spans", [])})
                if new_lines:
                    page_blocks.append({"lines": new_lines})

            # Collect body size baseline per page (fallback to document median if too few)
            sizes = []
            for b in page_blocks:
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        s, _, _, t = span_style(span)
                        if t.strip():
                            sizes.append(s)
            if len(sizes) < 5:
                # fallback: scan this page's dict more broadly
                for blk in info.get("blocks", []):
                    for ln in blk.get("lines", []):
                        for sp in ln.get("spans", []):
                            s, _, _, t = span_style(sp)
                            if t.strip():
                                sizes.append(s)
            page_body = median(sizes) if sizes else 11.0

            # Convert blocks on this page
            for b in page_blocks:
                md_lines.extend(
                    block_to_markdown_lines(
                        b,
                        body_size=page_body,
                        caps_to_headings=caps_headings,
                        heading_size_ratio=heading_size_ratio,
                        linkify_urls=True,
                    )
                )

            # Export images from this page and add simple refs
            if export_images:
                images = page.get_images(full=True)
                if images:
                    if assets_dir is None:
                        out_path = Path(output_md)
                        assets_dir = out_path.with_name(out_path.stem + "_assets")
                        assets_dir.mkdir(parents=True, exist_ok=True)
                    md_lines.append("")
                    md_lines.append(f"**Images from page {pno + 1}:**")
                    for idx, img in enumerate(images, start=1):
                        xref = img[0]
                        pix = fitz.Pixmap(doc, xref)
                        if pix.n > 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        fname = assets_dir / f"img_{pno + 1:03d}_{idx:02d}.png"
                        pix.save(str(fname))
                        # relative path so MD works anywhere
                        rel_path = assets_dir.name + "/" + fname.name
                        md_lines.append(f"- ![p{pno + 1}-{idx}]({rel_path})")
                        pix = None  # free memory
                    md_lines.append("")

            # Page break marker
            if insert_page_breaks and pno < pages_to_process - 1:
                md_lines.extend(["---", ""])  # horizontal rule style

        except Exception as e:
            # Per-page error recovery
            md_lines.append(f"\n**[Error on page {pno + 1}: {e}]**\n")
            # continue to next page

        # Update progress
        if progress_cb:
            progress_cb(pno + 1, pages_to_process)

    # Join and tidy
    md = "\n".join(md_lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    if defragment_short:
        md = defragment_orphans(md, max_len=orphan_max_len)

    # Final tidy: spacing around punctuation
    md = re.sub(r"\s+([,.;:?!])", r"\1", md)
    
        """
    ! Added final post-processing steps:
    ! For more information see...
    ! - `fix_hyphenation()`: Function deactivated to be replaced by less aggressive hyphen unwrap
    ! - drop-in helpers now live in `transform.py` (see `two_pass_unwrap` & `convert_simple_callouts`)
    
    """
    md = two_pass_unwrap(md, aggressive_hyphen=False, protect_code=True)   # hyphen unwrap + non-sentence reflow (paragraphs preserved)
    md = convert_simple_callouts(md)                    # turn "Note:/Tip:/Warning:/Example:" into callouts

    # Sink 
    Path(output_md).write_text(md, encoding="utf-8")


# ============================= GUI =============================
class PDF2MDApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF → Markdown")
        self.geometry("860x640")
        self.minsize(840, 600)

        # State variables
        cfg = load_cfg()
        self.input_path = tk.StringVar(value=cfg.get("input_path", ""))
        self.output_path = tk.StringVar(value=cfg.get("output_path", ""))
        self.caps_to_headings = tk.BooleanVar(value=cfg.get("caps_to_headings", True))
        self.defragment = tk.BooleanVar(value=cfg.get("defragment", True))
        self.heading_ratio = tk.StringVar(value=str(cfg.get("heading_ratio", 1.15)))
        self.orphan_len = tk.StringVar(value=str(cfg.get("orphan_len", 45)))
        self.insert_page_breaks = tk.BooleanVar(value=cfg.get("insert_page_breaks", False))
        self.remove_headers_footers = tk.BooleanVar(value=cfg.get("remove_headers_footers", True))
        self.export_images = tk.BooleanVar(value=cfg.get("export_images", False))
        self.preview_only = tk.BooleanVar(value=cfg.get("preview_only", False))

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 8}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True)

        # Input
        row_in = ttk.Frame(frm); row_in.pack(fill="x", **pad)
        ttk.Label(row_in, text="Input PDF:").pack(side="left")
        ttk.Entry(row_in, textvariable=self.input_path).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row_in, text="Browse…", command=self.choose_input).pack(side="left")

        # Output
        row_out = ttk.Frame(frm); row_out.pack(fill="x", **pad)
        ttk.Label(row_out, text="Output .md:").pack(side="left")
        ttk.Entry(row_out, textvariable=self.output_path).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row_out, text="Save As…", command=self.choose_output).pack(side="left")

        # Options (left column)
        box = ttk.LabelFrame(frm, text="Options"); box.pack(fill="x", **pad)
        ttk.Checkbutton(box, text="Promote ALL-CAPS lines to headings", variable=self.caps_to_headings).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(box, text="Merge short orphan fragments", variable=self.defragment).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(box, text="Insert page breaks (---)", variable=self.insert_page_breaks).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(box, text="Remove repeating header/footer", variable=self.remove_headers_footers).grid(row=3, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(box, text="Export images to _assets/", variable=self.export_images).grid(row=4, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(box, text="Preview first 3 pages only", variable=self.preview_only).grid(row=5, column=0, sticky="w", padx=8, pady=6)

        # Options (right column values)
        ttk.Label(box, text="Heading size ratio (≥ body × ratio → heading):").grid(row=0, column=1, sticky="e", padx=8)
        ttk.Entry(box, width=8, textvariable=self.heading_ratio).grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(box, text="Orphan max length (chars):").grid(row=1, column=1, sticky="e", padx=8)
        ttk.Entry(box, width=8, textvariable=self.orphan_len).grid(row=1, column=2, sticky="w", padx=4)

        # Convert button + progress
        act = ttk.Frame(frm); act.pack(fill="x", **pad)
        self.btn_convert = ttk.Button(act, text="Convert", command=self.start_convert); self.btn_convert.pack(side="left")
        self.progress = ttk.Progressbar(act, mode="determinate"); self.progress.pack(side="left", fill="x", expand=True, padx=12)

        # Log
        logf = ttk.LabelFrame(frm, text="Log"); logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=14, wrap="word"); self.log.pack(fill="both", expand=True, padx=6, pady=6)
        ttk.Label(frm, text="Output is Obsidian-ready (clean paragraphs, #/## headings, **bold**/*italics* when available).")\
            .pack(anchor="w", padx=12, pady=(0, 10))

    # --------- UI handlers ---------
    def choose_input(self):
        path = filedialog.askopenfilename(title="Select PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if path:
            self.input_path.set(path)
            if not self.output_path.get():
                base = os.path.splitext(path)[0]
                self.output_path.set(base + ".md")

    def choose_output(self):
        path = filedialog.asksaveasfilename(title="Save Markdown As", defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if path:
            self.output_path.set(path)

    def start_convert(self):
        in_path = self.input_path.get().strip()
        out_path = self.output_path.get().strip()
        if not in_path:
            messagebox.showwarning("Missing input", "Please choose an input PDF."); return
        if not out_path:
            messagebox.showwarning("Missing output", "Please choose where to save the Markdown."); return
        if not os.path.exists(in_path):
            messagebox.showerror("Input not found", f"File not found:\n{in_path}"); return
        if not in_path.lower().endswith('.pdf'):
            messagebox.showwarning("Invalid file", "Please select a PDF file."); return

        # Validate options
        try:
            ratio = float(str(self.heading_ratio.get()).strip())
            if ratio <= 1.0:
                raise ValueError
        except Exception:
            messagebox.showwarning("Invalid setting", "Heading size ratio must be a number > 1.0 (e.g., 1.15)."); return
        try:
            orphan_len = int(str(self.orphan_len.get()).strip())
            if orphan_len < 10:
                raise ValueError
        except Exception:
            messagebox.showwarning("Invalid setting", "Orphan max length should be a reasonable integer (e.g., 45)."); return

        # Prepare UI
        self.log_clear()
        self.log_write(f"Input : {in_path}\n")
        self.log_write(f"Output: {out_path}\n")
        self.log_write(
            "Options → "
            f"CAPS→Headings={self.caps_to_headings.get()}, "
            f"Defragment={self.defragment.get()}, "
            f"HeadingRatio={ratio}, OrphanLen={orphan_len}, "
            f"PageBreaks={self.insert_page_breaks.get()}, RemoveHF={self.remove_headers_footers.get()}, ExportImages={self.export_images.get()}, PreviewOnly={self.preview_only.get()}\n\n"
        )
        self.btn_convert.config(state="disabled")
        self.progress.configure(value=0, maximum=100)

        # Save config early
        save_cfg({
            "input_path": in_path,
            "output_path": out_path,
            "caps_to_headings": bool(self.caps_to_headings.get()),
            "defragment": bool(self.defragment.get()),
            "heading_ratio": ratio,
            "orphan_len": orphan_len,
            "insert_page_breaks": bool(self.insert_page_breaks.get()),
            "remove_headers_footers": bool(self.remove_headers_footers.get()),
            "export_images": bool(self.export_images.get()),
            "preview_only": bool(self.preview_only.get()),
        })

        # Run conversion in a worker thread
        t = threading.Thread(target=self._do_convert, args=(in_path, out_path, ratio, orphan_len), daemon=True)
        t.start()

    def _progress_cb(self, done_pages: int, total_pages: int):
        if total_pages <= 0:
            return
        pct = int((done_pages / total_pages) * 100)
        self.progress.configure(value=pct)

    def _do_convert(self, in_path: str, out_path: str, ratio: float, orphan_len: int):
        try:
            if fitz is None:
                raise RuntimeError("PyMuPDF (fitz) is not installed.\n\nInstall with:\n    pip install pymupdf")
            pdf_to_markdown(
                in_path,
                out_path,
                caps_headings=bool(self.caps_to_headings.get()),
                defragment_short=bool(self.defragment.get()),
                heading_size_ratio=ratio,
                orphan_max_len=orphan_len,
                insert_page_breaks=bool(self.insert_page_breaks.get()),
                remove_headers_footers=bool(self.remove_headers_footers.get()),
                export_images=bool(self.export_images.get()),
                preview_only=bool(self.preview_only.get()),
                progress_cb=lambda d, t: self.after(0, self._progress_cb, d, t),
            )
            self.after(0, self._done_ok, out_path)
        except Exception as e:
            self.after(0, self._done_err, e)

    def _done_ok(self, out_path: str):
        self.progress.configure(value=100)
        self.btn_convert.config(state="normal")
        self.log_write("✔ Conversion complete.\n")
        self.log_write(f"Saved: {out_path}\n")
        messagebox.showinfo("Done", "Conversion complete!")

    def _done_err(self, e: Exception):
        self.btn_convert.config(state="normal")
        self.log_write(f"✖ Error: {e}\n")
        messagebox.showerror("Error", f"Conversion failed:\n\n{e}")

    def log_write(self, s: str):
        self.log.insert("end", s)
        self.log.see("end")

    def log_clear(self):
        self.log.delete("1.0", "end")

# ============================ Entry point ============================
if __name__ == "__main__":
    app = PDF2MDApp()
    app.mainloop()
