"""Tkinter GUI for pdfmd – UI/UX with light/dark themes, profiles, and cancel support.

This GUI is a front-end for the offline pdfmd engine. It:

- Lets the user pick an input PDF and output Markdown file.
- Exposes the core Options (OCR, preview, headings, defrag, etc.).
- Streams pipeline logs to a console-like panel.
- Shows a determinate progress bar and status line.
- Allows the user to CANCEL a long-running conversion (e.g. OCR).
- Supports Light and Dark themes (dark is Obsidian-style, not grey).
- Remembers theme, paths, and options globally via a small JSON config.
- Provides conversion profiles (built-in and user-defined).
- Supports keyboard shortcuts for common actions.

Run as:
    python -m pdfmd.app_gui
or:
    python app_gui.py      (from the package folder)
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# Optional PyMuPDF import for password probing (GUI pre-checks)
try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - optional
    fitz = None  # type: ignore

# --- Robust imports: package or script mode ---------------------------------
try:
    # Package style, e.g. `python -m pdfmd.app_gui`
    from pdfmd.models import Options
    from pdfmd.pipeline import pdf_to_markdown
    from pdfmd.utils import os_display_path
except ImportError:  # fallback for `python app_gui.py`
    import sys

    _HERE = Path(__file__).resolve().parent
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    from models import Options
    from pipeline import pdf_to_markdown
    from utils import os_display_path
# ---------------------------------------------------------------------------

# --- High DPI awareness for Windows -----------------------------------------
if platform.system() == "Windows":
    try:
        from ctypes import windll
        # 0 = unaware, 1 = system DPI aware, 2 = per-monitor DPI aware
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            windll.user32.SetProcessDPIAware()
    except Exception:
        # Best-effort only; if this fails we just fall back to default scaling.
        pass

OCR_CHOICES = ("off", "auto", "tesseract", "ocrmypdf")
CONFIG_PATH = Path.home() / ".pdfmd_gui.json"


DEFAULT_OPTIONS = {
    "ocr_mode": OCR_CHOICES[0],
    "preview": False,
    "export_images": False,
    "page_breaks": False,
    "rm_edges": True,
    "caps_to_headings": True,
    "defrag": True,
    "heading_ratio": 1.15,
    "orphan_len": 45,
}

BUILTIN_PROFILES = {
    "Default": DEFAULT_OPTIONS,
    "Academic article": {
        "ocr_mode": "auto",
        "preview": False,
        "export_images": False,
        "page_breaks": False,
        "rm_edges": True,
        "caps_to_headings": True,
        "defrag": True,
        "heading_ratio": 1.10,
        "orphan_len": 60,
    },
    "Slides / handouts": {
        "ocr_mode": "auto",
        "preview": False,
        "export_images": True,
        "page_breaks": True,
        "rm_edges": False,
        "caps_to_headings": False,
        "defrag": False,
        "heading_ratio": 1.20,
        "orphan_len": 40,
    },
    "Scan-heavy / OCR-first": {
        "ocr_mode": "tesseract",
        "preview": False,
        "export_images": False,
        "page_breaks": False,
        "rm_edges": True,
        "caps_to_headings": False,
        "defrag": True,
        "heading_ratio": 1.20,
        "orphan_len": 50,
    },
}


def _load_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _save_config(data: dict) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class PdfMdApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        # Adjust Tk scaling based on actual DPI so the UI stays sharp on HiDPI displays.
        try:
            # 1 inch in pixels divided by 72 dpi gives the scaling factor Tk should use.
            self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        except Exception:
            # If anything goes wrong here, we silently fall back to Tk's default scaling.
            pass

        self.title("PDF → Markdown (Offline, OCR-capable)")
        self.geometry("900x560")
        self.minsize(840, 520)

        self._worker: threading.Thread | None = None
        self._cancel_requested: bool = False
        self._last_output_path: str | None = None
        self.custom_profiles: dict[str, dict] = {}

        self._init_style()
        self._build_vars()
        self._load_config()
        self._build_ui()
        self._bind_shortcuts()

    # ------------------------------------------------------------------ style
    def _init_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Status.TLabel", font=("Segoe UI", 9))
        style.configure("StatusError.TLabel", font=("Segoe UI", 9))
        style.configure("StatusInfo.TLabel", font=("Segoe UI", 9))

        style.configure("Accent.TButton", padding=(14, 6))
        style.map(
            "Accent.TButton",
            foreground=[("!disabled", "white")],
            background=[
                ("!disabled", "#7C3AED"),
                ("pressed", "#5B21B6"),
                ("active", "#6D28D9"),
            ],
        )

        style.configure("Danger.TButton", padding=(12, 5))
        style.map(
            "Danger.TButton",
            foreground=[("!disabled", "white")],
            background=[
                ("!disabled", "#DC2626"),
                ("pressed", "#7F1D1D"),
                ("active", "#B91C1C"),
            ],
        )

        style.configure("Card.TFrame", relief="flat", borderwidth=0)

        self._bg_dark = "#0B1020"
        self._bg_light = "#F2F2F5"
        self._fg_dark = "#E5E7EB"
        self._fg_light = "#111827"
        self._accent = "#7C3AED"

    # ------------------------------------------------------------------ vars
    def _build_vars(self) -> None:
        self.theme_var = tk.StringVar(value="dark")
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.ocr_var = tk.StringVar(value=DEFAULT_OPTIONS["ocr_mode"])
        self.preview_var = tk.BooleanVar(value=DEFAULT_OPTIONS["preview"])
        self.export_images_var = tk.BooleanVar(
            value=DEFAULT_OPTIONS["export_images"]
        )
        self.page_breaks_var = tk.BooleanVar(
            value=DEFAULT_OPTIONS["page_breaks"]
        )
        self.rm_edges_var = tk.BooleanVar(value=DEFAULT_OPTIONS["rm_edges"])
        self.caps_to_headings_var = tk.BooleanVar(
            value=DEFAULT_OPTIONS["caps_to_headings"]
        )
        self.defrag_var = tk.BooleanVar(value=DEFAULT_OPTIONS["defrag"])
        self.heading_ratio_var = tk.DoubleVar(
            value=DEFAULT_OPTIONS["heading_ratio"]
        )
        self.orphan_len_var = tk.IntVar(value=DEFAULT_OPTIONS["orphan_len"])
        self.profile_var = tk.StringVar(value="Default")
        self.status_var = tk.StringVar(value="Ready.")
        self.progress_var = tk.DoubleVar(value=0.0)

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        self._apply_theme()

        main = ttk.Frame(self, padding=12, style="Card.TFrame")
        main.pack(fill="both", expand=True)

        top_frame = ttk.Frame(main)
        top_frame.pack(fill="x", pady=(0, 8))

        theme_btn = ttk.Button(
            top_frame,
            text="☾ Theme",
            width=10,
            command=self._toggle_theme,
        )
        theme_btn.pack(side="left")

        prof_label = ttk.Label(top_frame, text="Profile:")
        prof_label.pack(side="left", padx=(12, 4))

        profile_cb = ttk.Combobox(
            top_frame,
            textvariable=self.profile_var,
            values=list(BUILTIN_PROFILES.keys()),
            state="readonly",
            width=24,
        )
        profile_cb.pack(side="left")

        ttk.Button(
            top_frame, text="Save profile…", command=self._save_profile_dialog
        ).pack(side="left", padx=4)

        ttk.Button(
            top_frame, text="Delete profile", command=self._delete_profile_dialog
        ).pack(side="left")

        ttk.Button(
            top_frame, text="About", command=self._show_about
        ).pack(side="right", padx=(8, 0))

        paths_frame = ttk.LabelFrame(main, text="Paths", padding=8)
        paths_frame.pack(fill="x")

        in_label = ttk.Label(paths_frame, text="Input PDF:")
        in_label.grid(row=0, column=0, sticky="w")

        in_entry = ttk.Entry(paths_frame, textvariable=self.input_var)
        in_entry.grid(row=0, column=1, sticky="ew", padx=(6, 6))
        in_entry.focus_set()

        in_btn = ttk.Button(
            paths_frame, text="Browse…", width=10, command=self._browse_input
        )
        in_btn.grid(row=0, column=2, sticky="e")

        out_label = ttk.Label(paths_frame, text="Output Markdown:")
        out_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        out_entry = ttk.Entry(paths_frame, textvariable=self.output_var)
        out_entry.grid(row=1, column=1, sticky="ew", padx=(6, 6), pady=(6, 0))

        out_btn = ttk.Button(
            paths_frame,
            text="Browse…",
            width=10,
            command=self._browse_output,
        )
        out_btn.grid(row=1, column=2, sticky="e", pady=(6, 0))

        paths_frame.columnconfigure(1, weight=1)

        opts_frame = ttk.LabelFrame(main, text="Conversion options", padding=8)
        opts_frame.pack(fill="x", pady=(8, 8))

        o_row = 0

        ttk.Label(opts_frame, text="OCR mode:").grid(
            row=o_row, column=0, sticky="w"
        )
        ocr_box = ttk.Combobox(
            opts_frame,
            textvariable=self.ocr_var,
            values=OCR_CHOICES,
            state="readonly",
            width=12,
        )
        ocr_box.grid(row=o_row, column=1, sticky="w")
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Preview only (first few pages)",
            variable=self.preview_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Export images",
            variable=self.export_images_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w")
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Insert page breaks (--- between pages)",
            variable=self.page_breaks_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w")
        o_row += 1

        ttk.Separator(opts_frame, orient="horizontal").grid(
            row=o_row, column=0, columnspan=4, sticky="ew", pady=6
        )
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Remove repeating headers/footers",
            variable=self.rm_edges_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w")
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Promote ALL CAPS lines to headings",
            variable=self.caps_to_headings_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w")
        o_row += 1

        ttk.Checkbutton(
            opts_frame,
            text="Defragment short lines into paragraphs",
            variable=self.defrag_var,
        ).grid(row=o_row, column=0, columnspan=2, sticky="w")
        o_row += 1

        ttk.Label(opts_frame, text="Heading size ratio:").grid(
            row=o_row, column=0, sticky="w", pady=(4, 0)
        )

        heading_scale = ttk.Scale(
            opts_frame,
            from_=1.0,
            to=2.5,
            orient="horizontal",
            variable=self.heading_ratio_var,
        )
        heading_scale.grid(row=o_row, column=1, sticky="ew", padx=(8, 0))
        o_row += 1

        ttk.Label(opts_frame, text="Orphan max length:").grid(
            row=o_row, column=0, sticky="w", pady=(2, 0)
        )
        orphan_spin = ttk.Spinbox(
            opts_frame,
            from_=10,
            to=120,
            textvariable=self.orphan_len_var,
            width=6,
        )
        orphan_spin.grid(row=o_row, column=1, sticky="w", padx=(8, 0))
        o_row += 1

        opts_frame.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(main, text="Log / status", padding=8)
        log_frame.pack(fill="both", expand=True)

        text = tk.Text(
            log_frame,
            wrap="word",
            height=10,
            state="disabled",
            font=("Consolas", 9),
        )
        text.pack(fill="both", expand=True, side="left")
        self.log_text = text

        scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=text.yview
        )
        scroll.pack(fill="y", side="right")
        text["yscrollcommand"] = scroll.set

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(6, 0))

        progress = ttk.Progressbar(
            bottom, variable=self.progress_var, maximum=100
        )
        progress.pack(fill="x", side="left", expand=True)

        btn_frame = ttk.Frame(bottom)
        btn_frame.pack(side="right", padx=(8, 0))

        self.convert_btn = ttk.Button(
            btn_frame,
            text="Convert → Markdown",
            style="Accent.TButton",
            command=self._on_convert,
            width=22,
        )
        self.convert_btn.pack(side="left")

        self.cancel_btn = ttk.Button(
            btn_frame,
            text="Cancel",
            style="Danger.TButton",
            command=self._on_cancel,
            width=10,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=(6, 0))

        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", side="bottom", pady=(0, 0), padx=6)

        self.status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
            anchor="w",
        )
        self.status_label.pack(fill="x", side="left", expand=True)

        open_btn = ttk.Button(
            status_frame, text="Open folder", command=self._open_output_folder
        )
        open_btn.pack(side="right")

        self._update_theme_widgets()

    # ------------------------------------------------------------------ theme
    def _apply_theme(self) -> None:
        theme = self.theme_var.get()
        if theme == "dark":
            bg = self._bg_dark
            fg = self._fg_dark
        else:
            bg = self._bg_light
            fg = self._fg_light

        self.configure(bg=bg)

        style = ttk.Style(self)
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=bg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("TButton", background=bg)
        style.configure("TCombobox", fieldbackground=bg)

        self._text_bg = "#020617" if theme == "dark" else "#FFFFFF"
        self._text_fg = "#E5E7EB" if theme == "dark" else "#111827"

        if hasattr(self, "log_text"):
            self.log_text.configure(
                background=self._text_bg,
                foreground=self._text_fg,
                insertbackground=self._text_fg,
            )

    def _toggle_theme(self) -> None:
        current = self.theme_var.get()
        self.theme_var.set("light" if current == "dark" else "dark")
        self._apply_theme()
        self._update_theme_widgets()
        self._save_config()

    def _update_theme_widgets(self) -> None:
        theme = self.theme_var.get()
        if theme == "dark":
            self.status_label.configure(
                style="StatusInfo.TLabel", foreground="#A5B4FC"
            )
        else:
            self.status_label.configure(
                style="Status.TLabel", foreground=self._fg_light
            )

    # ------------------------------------------------------------------ config
    def _load_config(self) -> None:
        data = _load_config()
        gui = data.get("gui", {})
        if not isinstance(gui, dict):
            gui = {}

        theme = gui.get("theme")
        if isinstance(theme, str) and theme in ("light", "dark"):
            self.theme_var.set(theme)

        last_in = gui.get("last_input")
        if isinstance(last_in, str):
            self.input_var.set(last_in)

        last_out = gui.get("last_output")
        if isinstance(last_out, str):
            self.output_var.set(last_out)

        profile = gui.get("profile")
        if isinstance(profile, str) and profile in BUILTIN_PROFILES:
            self.profile_var.set(profile)

        opts = gui.get("options")
        if isinstance(opts, dict):
            self._apply_options_to_vars(opts)

        custom = gui.get("custom_profiles")
        if isinstance(custom, dict):
            self.custom_profiles = {
                k: v for k, v in custom.items() if isinstance(v, dict)
            }

        self._apply_theme()
        self._update_theme_widgets()

    def _save_config(self) -> None:
        gui_data = {
            "theme": self.theme_var.get(),
            "last_input": self.input_var.get(),
            "last_output": self.output_var.get(),
            "profile": self.profile_var.get(),
            "options": self._collect_options_from_vars(),
            "custom_profiles": self.custom_profiles,
        }

        data = _load_config()
        data["gui"] = gui_data
        _save_config(data)

    # ------------------------------------------------------------------ profiles
    def _collect_options_from_vars(self) -> dict:
        return {
            "ocr_mode": self.ocr_var.get(),
            "preview": bool(self.preview_var.get()),
            "export_images": bool(self.export_images_var.get()),
            "page_breaks": bool(self.page_breaks_var.get()),
            "rm_edges": bool(self.rm_edges_var.get()),
            "caps_to_headings": bool(self.caps_to_headings_var.get()),
            "defrag": bool(self.defrag_var.get()),
            "heading_ratio": float(self.heading_ratio_var.get()),
            "orphan_len": int(self.orphan_len_var.get()),
        }

    def _apply_options_to_vars(self, opts: dict) -> None:
        o = DEFAULT_OPTIONS.copy()
        o.update(opts)

        self.ocr_var.set(o["ocr_mode"])
        self.preview_var.set(bool(o["preview"]))
        self.export_images_var.set(bool(o["export_images"]))
        self.page_breaks_var.set(bool(o["page_breaks"]))
        self.rm_edges_var.set(bool(o["rm_edges"]))
        self.caps_to_headings_var.set(bool(o["caps_to_headings"]))
        self.defrag_var.set(bool(o["defrag"]))
        self.heading_ratio_var.set(float(o["heading_ratio"]))
        self.orphan_len_var.set(int(o["orphan_len"]))

    def _save_profile_dialog(self) -> None:
        name = simpledialog.askstring(
            "Save profile",
            "Enter a name for this profile:",
            parent=self,
        )
        if not name:
            return

        if name in BUILTIN_PROFILES:
            messagebox.showerror(
                "Error",
                "You cannot overwrite a built-in profile.",
                parent=self,
            )
            return

        opts = self._collect_options_from_vars()
        self.custom_profiles[name] = opts

        all_profiles = list(BUILTIN_PROFILES.keys()) + list(
            self.custom_profiles.keys()
        )
        self.profile_var.set(name)

        frame = self.children.get("!frame")
        if frame:
            top_frame = frame.children.get("!frame")
            if top_frame:
                for child in top_frame.winfo_children():
                    if isinstance(child, ttk.Combobox):
                        child["values"] = all_profiles
                        break

        self._save_config()
        self._log(f"Profile '{name}' saved.")

    def _delete_profile_dialog(self) -> None:
        name = self.profile_var.get()
        if name in BUILTIN_PROFILES:
            messagebox.showinfo(
                "Cannot delete",
                "Built-in profiles cannot be deleted.",
                parent=self,
            )
            return

        if name not in self.custom_profiles:
            messagebox.showinfo(
                "Not found",
                "No custom profile with this name exists.",
                parent=self,
            )
            return

        if not messagebox.askyesno(
            "Delete profile",
            f"Delete custom profile '{name}'?",
            parent=self,
        ):
            return

        del self.custom_profiles[name]

        all_profiles = list(BUILTIN_PROFILES.keys()) + list(
            self.custom_profiles.keys()
        )

        self.profile_var.set("Default")

        frame = self.children.get("!frame")
        if frame:
            top_frame = frame.children.get("!frame")
            if top_frame:
                for child in top_frame.winfo_children():
                    if isinstance(child, ttk.Combobox):
                        child["values"] = all_profiles
                        break

        self._save_config()
        self._log(f"Profile '{name}' deleted.")

    # ------------------------------------------------------------------ log
    def _log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ browse
    def _browse_input(self) -> None:
        initial_dir = os.path.dirname(self.input_var.get()) or os.getcwd()
        try:
            fname = filedialog.askopenfilename(
                parent=self,
                title="Select input PDF",
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
                initialdir=initial_dir,
            )
        except Exception:
            fname = filedialog.askopenfilename(
                parent=self,
                title="Select input PDF",
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            )

        if fname:
            self.input_var.set(fname)
            if not self.output_var.get():
                stem = Path(fname).with_suffix(".md").name
                self.output_var.set(str(Path(fname).with_suffix(".md")))
            self._save_config()

    def _browse_output(self) -> None:
        current = self.output_var.get().strip()
        initial_dir = os.path.dirname(current) or os.path.dirname(
            self.input_var.get()
        ) or os.getcwd()
        initial_name = os.path.basename(current) or "output.md"

        try:
            fname = filedialog.asksaveasfilename(
                parent=self,
                title="Select output Markdown file",
                defaultextension=".md",
                filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
                initialdir=initial_dir,
                initialfile=initial_name,
            )
        except Exception:
            fname = filedialog.asksaveasfilename(
                parent=self,
                title="Select output Markdown file",
                defaultextension=".md",
                filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
            )

        if fname:
            self.output_var.set(fname)
            self._save_config()

    # ------------------------------------------------------------------ open folder
    def _open_output_folder(self) -> None:
        out_path = self.output_var.get().strip()
        if out_path:
            folder = os.path.dirname(out_path)
        elif self._last_output_path:
            folder = os.path.dirname(self._last_output_path)
        else:
            folder = ""

        if not folder:
            messagebox.showinfo(
                "No output yet",
                "There is no known output folder yet.",
                parent=self,
            )
            return

        folder_path = Path(folder)
        if not folder_path.exists():
            messagebox.showerror(
                "Folder not found",
                f"The folder {os_display_path(folder_path)} does not exist.",
                parent=self,
            )
            return

        try:
            if platform.system() == "Windows":
                os.startfile(str(folder_path))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(folder_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder_path)], check=False)
        except Exception as exc:
            messagebox.showerror(
                "Error opening folder",
                f"Could not open folder: {exc}",
                parent=self,
            )

    # ------------------------------------------------------------------ shortcuts
    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda e: self._browse_input())
        self.bind("<Control-O>", lambda e: self._browse_input())
        self.bind("<Control-Shift-O>", lambda e: self._browse_output())
        self.bind("<Control-Return>", lambda e: self._on_convert())
        self.bind("<Escape>", lambda e: self._on_cancel())

    # ------------------------------------------------------------------ status
    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self.status_var.set(msg)
        if error:
            self.status_label.configure(style="StatusError.TLabel")
        else:
            self.status_label.configure(style="StatusInfo.TLabel")

    # ------------------------------------------------------------------ convert
    def _build_options(self) -> Options:
        opts = Options()
        opts.ocr_mode = self.ocr_var.get()
        opts.preview_only = bool(self.preview_var.get())
        opts.insert_page_breaks = bool(self.page_breaks_var.get())
        opts.export_images = bool(self.export_images_var.get())
        opts.remove_repeating_edges = bool(self.rm_edges_var.get())
        opts.caps_to_headings = bool(self.caps_to_headings_var.get())
        opts.defragment_short_lines = bool(self.defrag_var.get())
        opts.heading_size_ratio = float(self.heading_ratio_var.get())
        opts.orphan_max_length = int(self.orphan_len_var.get())
        return opts

    def _on_convert(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        inp = self.input_var.get().strip()
        outp = self.output_var.get().strip()

        if not inp:
            messagebox.showerror(
                "Missing input",
                "Please choose an input PDF file.",
                parent=self,
            )
            return

        in_path = Path(inp)
        if not in_path.is_file():
            messagebox.showerror(
                "Input not found",
                f"The input file {os_display_path(in_path)} does not exist.",
                parent=self,
            )
            return

        if not outp:
            outp = str(in_path.with_suffix(".md"))
            self.output_var.set(outp)

        out_path = Path(outp)
        if out_path.exists():
            if not messagebox.askyesno(
                "Overwrite?",
                f"The file {os_display_path(out_path)} already exists.\n"
                "Overwrite it?",
                parent=self,
            ):
                return

        self._save_config()

        opts = self._build_options()

        self._cancel_requested = False
        self.progress_var.set(0.0)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.convert_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        self._set_status("Converting…")

        def log_cb(msg: str) -> None:
            self.log_text.after(0, self._log, msg)

        def progress_cb(done: int, total: int) -> None:
            pct = 0.0
            if total > 0:
                pct = max(0.0, min(100.0, (done / total) * 100.0))
            self.progress_var.set(pct)

        def worker() -> None:
            pdf_password: str | None = None

            if fitz is not None:
                try:
                    with fitz.open(str(in_path)) as doc:
                        if doc.needsPass:
                            self.log_text.after(
                                0,
                                self._log,
                                "This PDF appears to be encrypted; password may be required.",
                            )
                except Exception:
                    pass

            def do_convert(password: str | None) -> None:
                pdf_to_markdown(
                    str(in_path),
                    str(out_path),
                    opts,
                    progress_cb=progress_cb,
                    log_cb=log_cb,
                    pdf_password=password,
                    cancel_flag=lambda: self._cancel_requested,
                )

            try:
                do_convert(pdf_password)
            except Exception as exc:
                if any(
                    s in str(exc).lower()
                    for s in [
                        "password required",
                        "password is required",
                        "incorrect pdf password",
                        "wrong password",
                        "cannot decrypt",
                        "encrypted",
                    ]
                ):
                    self.log_text.after(
                        0,
                        self._log,
                        "PDF is encrypted; prompting for password…",
                    )

                    def prompt_password() -> str | None:
                        return simpledialog.askstring(
                            "PDF password",
                            "This PDF appears to be encrypted.\n"
                            "Enter password:",
                            parent=self,
                            show="*",
                        )

                    pdf_password_local = self.log_text.after(0, prompt_password)
                    pdf_password_local = prompt_password()
                    if not pdf_password_local:
                        self.log_text.after(
                            0,
                            self._log,
                            "No password provided; conversion aborted.",
                        )
                        self._on_worker_done(error=True)
                        return
                    try:
                        do_convert(pdf_password_local)
                    except Exception as exc2:
                        self.log_text.after(
                            0,
                            self._log,
                            f"Error after password: {exc2}",
                        )
                        self._on_worker_done(error=True)
                        return
                else:
                    self.log_text.after(
                        0,
                        self._log,
                        f"Error: {exc}",
                    )
                    self._on_worker_done(error=True)
                    return

            self._last_output_path = str(out_path)
            self._on_worker_done(error=False)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_worker_done(self, *, error: bool) -> None:
        def update_ui() -> None:
            self.convert_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            if self._cancel_requested:
                self._set_status("Conversion cancelled.", error=True)
            elif error:
                self._set_status("Conversion failed – see log.", error=True)
            else:
                self._set_status("Conversion complete.")
            self.progress_var.set(100.0 if not error else 0.0)

        self.after(0, update_ui)

    def _on_cancel(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        self._cancel_requested = True
        self._set_status("Cancellation requested…")
        self._log("Cancellation requested. Waiting for current step to finish…")

    # ------------------------------------------------------------------ about
    def _show_about(self) -> None:
        msg = (
            "PDF → Markdown (pdfmd)\n"
            "\n"
            "Fast, local, privacy-first PDF → Markdown converter.\n"
            "This GUI is a thin wrapper around the offline engine.\n"
            "\n"
            "All processing happens on your machine.\n"
            "No uploads, no telemetry, no tracking.\n"
        )
        messagebox.showinfo("About pdfmd", msg, parent=self)


if __name__ == "__main__":
    app = PdfMdApp()
    app.mainloop()
