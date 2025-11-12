"""Tkinter GUI for pdfmd

This GUI is intentionally thin: it collects user options, shows OS-native
paths, streams logs, and displays a determinate progress bar while running the
pipeline.

It relies on the engine modules:
- pdfmd.models.Options
- pdfmd.pipeline.pdf_to_markdown
- pdfmd.utils.os_display_path

OCR is selectable (Off/Auto/Tesseract/OCRmyPDF). Optional image export and page
break markers are supported. Markdown always uses forward slashes for links, but
all GUI echoes use the OS-native separator for clarity.
"""
from __future__ import annotations

import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- robust imports: work as package OR as a loose script ---
try:
    # Package-style (when run via: python -m pdfmd.app_gui)
    from pdfmd.models import Options
    from pdfmd.pipeline import pdf_to_markdown
    from pdfmd.utils import os_display_path
except ImportError:
    # Script-style (when run via: python app_gui.py from the same folder)
    import os, sys
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    from models import Options
    from pipeline import pdf_to_markdown
    from utils import os_display_path
# --- end robust imports ---



OCR_CHOICES = ["off", "auto", "tesseract", "ocrmypdf"]


class PdfMdApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PDF → Markdown")
        self.geometry("820x520")
        self.minsize(760, 480)
        self._build_vars()
        self._build_ui()
        self._wire_events()

    # ------------------------------- State -------------------------------
    def _build_vars(self) -> None:
        self.in_path_var = tk.StringVar()
        self.out_path_var = tk.StringVar()
        self.ocr_var = tk.StringVar(value=OCR_CHOICES[0])
        self.preview_var = tk.BooleanVar(value=False)
        self.export_images_var = tk.BooleanVar(value=False)
        self.page_breaks_var = tk.BooleanVar(value=False)
        self.rm_edges_var = tk.BooleanVar(value=True)
        self.caps_to_headings_var = tk.BooleanVar(value=True)
        self.defrag_var = tk.BooleanVar(value=True)
        self.orphan_len_var = tk.IntVar(value=45)
        self.heading_ratio_var = tk.DoubleVar(value=1.15)

        self._worker: threading.Thread | None = None

    # ------------------------------- UI -------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 8}

        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True)

        # Paths
        paths = ttk.LabelFrame(root, text="Paths")
        paths.pack(fill=tk.X, **pad)

        # Input
        ttk.Label(paths, text="Input PDF:").grid(row=0, column=0, sticky="w")
        in_entry = ttk.Entry(paths, textvariable=self.in_path_var)
        in_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        in_btn = ttk.Button(paths, text="Browse…", command=self._choose_input)
        in_btn.grid(row=0, column=2, sticky="e")

        # Output
        ttk.Label(paths, text="Output .md:").grid(row=1, column=0, sticky="w")
        out_entry = ttk.Entry(paths, textvariable=self.out_path_var)
        out_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8))
        out_btn = ttk.Button(paths, text="Browse…", command=self._choose_output)
        out_btn.grid(row=1, column=2, sticky="e")

        paths.columnconfigure(1, weight=1)

        # Options
        opts = ttk.LabelFrame(root, text="Options")
        opts.pack(fill=tk.X, **pad)

        ttk.Label(opts, text="OCR:").grid(row=0, column=0, sticky="w")
        ocr_combo = ttk.Combobox(opts, textvariable=self.ocr_var, values=OCR_CHOICES, state="readonly", width=12)
        ocr_combo.grid(row=0, column=1, sticky="w", padx=(6, 18))

        ttk.Checkbutton(opts, text="Preview first 3 pages", variable=self.preview_var).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(opts, text="Export images", variable=self.export_images_var).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(opts, text="Insert page breaks (---)", variable=self.page_breaks_var).grid(row=0, column=4, sticky="w")

        ttk.Checkbutton(opts, text="Remove repeating header/footer", variable=self.rm_edges_var).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(opts, text="Promote CAPS to headings", variable=self.caps_to_headings_var).grid(row=1, column=2, sticky="w")
        ttk.Checkbutton(opts, text="Defragment short orphans", variable=self.defrag_var).grid(row=1, column=3, sticky="w")

        ttk.Label(opts, text="Heading size ratio").grid(row=2, column=0, sticky="w")
        hs = ttk.Spinbox(opts, from_=1.0, to=2.5, increment=0.05, textvariable=self.heading_ratio_var, width=6)
        hs.grid(row=2, column=1, sticky="w")

        ttk.Label(opts, text="Orphan max length").grid(row=2, column=2, sticky="w")
        om = ttk.Spinbox(opts, from_=10, to=120, increment=1, textvariable=self.orphan_len_var, width=6)
        om.grid(row=2, column=3, sticky="w")

        for c in range(5):
            opts.columnconfigure(c, weight=1)

        # Controls
        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, **pad)
        self.go_btn = ttk.Button(controls, text="Convert → Markdown", command=self._on_convert)
        self.go_btn.pack(side=tk.RIGHT)

        # Progress + log
        prog = ttk.LabelFrame(root, text="Progress")
        prog.pack(fill=tk.BOTH, expand=True, **pad)

        self.pbar = ttk.Progressbar(prog, orient=tk.HORIZONTAL, mode="determinate", maximum=100)
        self.pbar.pack(fill=tk.X, padx=8, pady=6)
        self.log_txt = tk.Text(prog, height=14, wrap="word")
        self.log_txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.log_txt.configure(state=tk.DISABLED)

    # ------------------------------ Events ------------------------------
    def _wire_events(self) -> None:
        self.in_path_var.trace_add("write", lambda *_: self._suggest_output())

    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(title="Select PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        # Cosmetic OS-native display for user clarity
        self.in_path_var.set(os_display_path(path))
        self._suggest_output()

    def _choose_output(self) -> None:
        init = self.out_path_var.get() or "output.md"
        path = filedialog.asksaveasfilename(title="Save Markdown as…", defaultextension=".md", initialfile=Path(init).name,
                                            filetypes=[("Markdown", "*.md"), ("All files", "*.*")])
        if not path:
            return
        self.out_path_var.set(os_display_path(path))

    def _suggest_output(self) -> None:
        p = self.in_path_var.get().strip()
        if not p:
            return
        try:
            q = Path(p)
            name = q.stem + ".md"
            out = q.with_name(name)
            self.out_path_var.set(os_display_path(out))
        except Exception:
            pass

    # --------------------------- Run conversion ---------------------------
    def _on_convert(self) -> None:
        inp = self.in_path_var.get().strip()
        outp = self.out_path_var.get().strip()
        if not inp:
            messagebox.showwarning("Missing input", "Please choose an input PDF.")
            return
        if not outp:
            messagebox.showwarning("Missing output", "Please choose an output .md path.")
            return
        self._lock_ui(True)
        self._clear_log()
        self._log(f"Input:  {os_display_path(inp)}")
        self._log(f"Output: {os_display_path(outp)}")
        opts = Options(
            ocr_mode=self.ocr_var.get(),
            preview_only=self.preview_var.get(),
            caps_to_headings=self.caps_to_headings_var.get(),
            defragment_short=self.defrag_var.get(),
            heading_size_ratio=float(self.heading_ratio_var.get()),
            orphan_max_len=int(self.orphan_len_var.get()),
            remove_headers_footers=self.rm_edges_var.get(),
            insert_page_breaks=self.page_breaks_var.get(),
            export_images=self.export_images_var.get(),
        )
        self._worker = threading.Thread(target=self._run_pipeline, args=(inp, outp, opts), daemon=True)
        self._worker.start()

    def _run_pipeline(self, inp: str, outp: str, opts: Options) -> None:
        try:
            pdf_to_markdown(inp, outp, opts, progress_cb=self._progress_cb, log_cb=self._log)
            self._log("Done.")
        except Exception as e:
            self._log(f"Error: {e}")
        finally:
            self.after(0, lambda: self._lock_ui(False))

    # ------------------------------ Callbacks ------------------------------
    def _log(self, msg: str) -> None:
        def append():
            self.log_txt.configure(state=tk.NORMAL)
            self.log_txt.insert(tk.END, str(msg) + "\n")
            self.log_txt.see(tk.END)
            self.log_txt.configure(state=tk.DISABLED)
        self.after(0, append)

    def _progress_cb(self, done: int, total: int) -> None:
        pct = 0
        try:
            if total > 0:
                pct = int((done / total) * 100)
        except Exception:
            pct = done if 0 <= done <= 100 else 0
        self.after(0, lambda: self.pbar.configure(value=pct))

    # ------------------------------ Helpers ------------------------------
    def _lock_ui(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.go_btn.configure(state=state)

    def _clear_log(self) -> None:
        self.log_txt.configure(state=tk.NORMAL)
        self.log_txt.delete("1.0", tk.END)
        self.log_txt.configure(state=tk.DISABLED)


if __name__ == "__main__":
    app = PdfMdApp()
    app.mainloop()
