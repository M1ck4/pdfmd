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
        "defrag": True,
        "heading_ratio": 1.20,
        "orphan_len": 45,
    },
    "Scan-heavy / OCR-first": {
        "ocr_mode": "tesseract",
        "preview": False,
        "export_images": False,
        "page_breaks": False,
        "rm_edges": True,
        "caps_to_headings": False,
        "defrag": True,
        "heading_ratio": 1.15,
        "orphan_len": 45,
    },
}


class UserCancelled(Exception):
    """Signal that the user requested cancellation."""
    pass


class ToolTip:
    """Very small helper for hover-tooltips on ttk widgets."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _event=None) -> None:
        if self._after_id is None:
            self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _show(self) -> None:
        if self._tip is not None:
            return
        try:
            x, y, _, h = self.widget.bbox("insert")
        except tk.TclError:
            x = y = h = 0
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + h + 12

        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        frame = ttk.Frame(tip, padding=(8, 4, 8, 4), relief="solid", borderwidth=1)
        frame.pack(fill="both", expand=True)
        label = ttk.Label(frame, text=self.text, justify="left", wraplength=320)
        label.pack()
        self._tip = tip

    def _hide(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class PdfMdApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

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
        self._wire_events()
        self._apply_theme()
        self._populate_profiles()

        self._set_status("Ready.", kind="info")

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
        style.map("Accent.TButton", foreground=[("disabled", "#999999")])

        style.configure("Card.TLabelframe", padding=(8, 6, 8, 10), borderwidth=1, relief="groove")
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 10, "bold"))

        style.configure("Log.TFrame", padding=(0, 4, 0, 0))

    # ------------------------------------------------------------------- state
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
        self.heading_ratio_var = tk.DoubleVar(value=1.15)
        self.orphan_len_var = tk.IntVar(value=45)

        # Dark is the default; Light is the alternate
        self.theme_var = tk.StringVar(value="Dark")

        # Profile name (built-in or custom)
        self.profile_var = tk.StringVar(value="Default")

    # ----------------------------------------------------------- config helpers
    def _load_config(self) -> None:
        """Load persisted settings, if any."""
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        theme = data.get("theme")
        if theme in ("Dark", "Light"):
            self.theme_var.set(theme)

        last_input = data.get("last_input")
        if isinstance(last_input, str):
            self.in_path_var.set(last_input)

        last_output = data.get("last_output")
        if isinstance(last_output, str):
            self.out_path_var.set(last_output)
            self._last_output_path = last_output

        opts = data.get("options")
        if isinstance(opts, dict):
            self._apply_options_dict(opts)

        profiles = data.get("profiles")
        if isinstance(profiles, dict):
            # Basic validation: only dict values
            self.custom_profiles = {
                name: opt for name, opt in profiles.items()
                if isinstance(opt, dict)
            }

    def _save_config(self) -> None:
        """Persist theme, paths, options, and custom profiles globally."""
        data = {
            "theme": self.theme_var.get(),
            "last_input": self.in_path_var.get().strip(),
            "last_output": self.out_path_var.get().strip(),
            "options": self._options_from_controls(),
            "profiles": self.custom_profiles,
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            # Fail silently; persistence is best-effort.
            pass

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(10, 8, 10, 8))
        root.pack(fill="both", expand=True)

        # Header: JUST the centered buttons
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 8))
        header.columnconfigure(0, weight=1)

        btn_row = ttk.Frame(header)
        btn_row.grid(row=0, column=0, sticky="n", pady=(2, 0))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.go_btn = ttk.Button(
            btn_row,
            text="Convert → Markdown",
            style="Accent.TButton",
            command=self._on_convert,
        )
        self.go_btn.grid(row=0, column=0, padx=(0, 6), sticky="e")

        self.stop_btn = ttk.Button(
            btn_row,
            text="Stop",
            command=self._on_cancel,
        )
        self.stop_btn.grid(row=0, column=1, sticky="w")
        self.stop_btn.configure(state="disabled")

        ToolTip(self.go_btn, "Convert (Ctrl+Enter)")
        ToolTip(self.stop_btn, "Stop (Esc)")

        # Paths card
        paths = ttk.Labelframe(root, text="Paths", style="Card.TLabelframe")
        paths.pack(fill="x", pady=(0, 8))

        ttk.Label(paths, text="Input PDF:").grid(row=0, column=0, sticky="w", padx=(2, 6), pady=4)
        in_entry = ttk.Entry(paths, textvariable=self.in_path_var)
        in_entry.grid(row=0, column=1, sticky="ew", pady=4)
        in_btn = ttk.Button(paths, text="Browse…", command=self._choose_input)
        in_btn.grid(row=0, column=2, sticky="e", padx=(6, 2), pady=4)

        ttk.Label(paths, text="Output .md:").grid(row=1, column=0, sticky="w", padx=(2, 6), pady=4)
        out_entry = ttk.Entry(paths, textvariable=self.out_path_var)
        out_entry.grid(row=1, column=1, sticky="ew", pady=4)
        out_btn = ttk.Button(paths, text="Browse…", command=self._choose_output)
        out_btn.grid(row=1, column=2, sticky="e", padx=(6, 2), pady=4)

        paths.columnconfigure(1, weight=1)

        ToolTip(
            in_entry,
            "Select the PDF you want to convert.\n"
            "Your file never leaves this machine — conversion is 100% local.",
        )
        ToolTip(out_entry, "Where the Markdown will be written.")

        # Options card
        opts = ttk.Labelframe(root, text="Options", style="Card.TLabelframe")
        opts.pack(fill="x", pady=(0, 8))

        # Row 0: Profile + Theme
        ttk.Label(opts, text="Profile:").grid(row=0, column=0, sticky="w", padx=(2, 4), pady=4)
        self.profile_combo = ttk.Combobox(
            opts,
            textvariable=self.profile_var,
            state="readonly",
            width=24,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", pady=4, padx=(0, 6))

        save_prof_btn = ttk.Button(opts, text="Save profile…", command=self._save_profile_dialog)
        save_prof_btn.grid(row=0, column=2, sticky="w", pady=4)

        del_prof_btn = ttk.Button(opts, text="Delete profile", command=self._delete_profile)
        del_prof_btn.grid(row=0, column=3, sticky="w", pady=4)

        # Theme selector at far right
        theme_frame = ttk.Frame(opts)
        theme_frame.grid(row=0, column=5, sticky="e", padx=(10, 2))
        ttk.Label(theme_frame, text="Theme:").pack(side="left", padx=(0, 4))
        theme_combo = ttk.Combobox(
            theme_frame,
            values=("Dark", "Light"),
            textvariable=self.theme_var,
            width=8,
            state="readonly",
        )
        theme_combo.pack(side="left")

        # Row 1: OCR + preview/export/breaks
        ttk.Label(opts, text="OCR mode:").grid(row=1, column=0, sticky="w", padx=(2, 4), pady=4)
        ocr_combo = ttk.Combobox(
            opts,
            values=OCR_CHOICES,
            textvariable=self.ocr_var,
            width=11,
            state="readonly",
        )
        ocr_combo.grid(row=1, column=1, sticky="w", pady=4, padx=(0, 10))
        ToolTip(
            ocr_combo,
            "off       – assume PDF has real text only.\n"
            "auto      – detect scanned PDFs and use OCR when needed.\n"
            "tesseract – force page-by-page OCR.\n"
            "ocrmypdf  – pre-process with OCRmyPDF, then extract text.",
        )

        ttk.Checkbutton(
            opts,
            text="Preview first 3 pages",
            variable=self.preview_var,
        ).grid(row=1, column=2, sticky="w", pady=4)
        ttk.Checkbutton(
            opts,
            text="Export images",
            variable=self.export_images_var,
        ).grid(row=1, column=3, sticky="w", pady=4)
        ttk.Checkbutton(
            opts,
            text="Insert page breaks (---)",
            variable=self.page_breaks_var,
        ).grid(row=1, column=4, sticky="w", pady=4)

        # Row 2: structural toggles
        ttk.Checkbutton(
            opts,
            text="Remove repeating header/footer",
            variable=self.rm_edges_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(
            opts,
            text="Promote CAPS to headings",
            variable=self.caps_to_headings_var,
        ).grid(row=2, column=2, sticky="w", pady=4)
        ttk.Checkbutton(
            opts,
            text="Defragment short orphans",
            variable=self.defrag_var,
        ).grid(row=2, column=3, sticky="w", pady=4)

        # Row 3: numeric tuning knobs (spinboxes)
        ttk.Label(opts, text="Heading size ratio").grid(row=3, column=0, sticky="w", padx=(2, 4), pady=4)
        heading_spin = ttk.Spinbox(
            opts,
            from_=1.0,
            to=2.5,
            increment=0.05,
            textvariable=self.heading_ratio_var,
            width=6,
        )
        heading_spin.grid(row=3, column=1, sticky="w", pady=4, padx=(0, 10))

        ttk.Label(opts, text="Orphan max length").grid(row=3, column=2, sticky="w", padx=(2, 4), pady=4)
        orphan_spin = ttk.Spinbox(
            opts,
            from_=10,
            to=120,
            increment=1,
            textvariable=self.orphan_len_var,
            width=6,
        )
        orphan_spin.grid(row=3, column=3, sticky="w", pady=4)

        ToolTip(
            heading_spin,
            "Lines whose average font size is ≥ body × this ratio\n"
            "are promoted to headings. Lower values → more headings.",
        )
        ToolTip(
            orphan_spin,
            "Short isolated lines up to this length (characters)\n"
            "will be merged back into the previous paragraph.",
        )

        for col in range(6):
            opts.columnconfigure(col, weight=1)

        # Progress + log region
        prog_card = ttk.Labelframe(root, text="Progress & log", style="Card.TLabelframe")
        prog_card.pack(fill="both", expand=True)

        top_prog = ttk.Frame(prog_card)
        top_prog.pack(fill="x", padx=(2, 2), pady=(4, 2))

        self.pbar = ttk.Progressbar(
            top_prog,
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.pbar.pack(fill="x", side="left", expand=True, padx=(0, 8))

        # container for status + link
        info_frame = ttk.Frame(top_prog)
        info_frame.pack(side="right")

        self.status_label = ttk.Label(info_frame, text="", style="Status.TLabel", anchor="e")
        self.status_label.pack(side="left", padx=(0, 6))

        self.open_folder_link = ttk.Label(
            info_frame,
            text="",
            style="StatusInfo.TLabel",
            cursor="hand2",
        )
        self.open_folder_link.pack(side="left")
        self.open_folder_link.bind("<Button-1>", self._on_open_folder)

        log_frame = ttk.Frame(prog_card, style="Log.TFrame")
        log_frame.pack(fill="both", expand=True, padx=(2, 2), pady=(0, 4))

        self.log_txt = tk.Text(
            log_frame,
            height=14,
            wrap="word",
            font=("Consolas", 9),
            undo=False,
            borderwidth=0,
            highlightthickness=0,
        )
        self.log_txt.pack(side="left", fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_txt.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_txt.configure(yscrollcommand=log_scroll.set, state="disabled")

    # --------------------------------------------------------------- event wire
    def _wire_events(self) -> None:
        self.in_path_var.trace_add("write", lambda *_: self._suggest_output())

        def on_theme_change(*_):
            self._apply_theme()
            self._save_config()

        self.theme_var.trace_add("write", on_theme_change)

        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        # Keyboard shortcuts
        self.bind_all("<Control-o>", lambda e: self._choose_input())
        self.bind_all("<Control-O>", lambda e: self._choose_input())
        self.bind_all("<Control-Shift-O>", lambda e: self._choose_output())
        self.bind_all("<Control-Return>", lambda e: self._on_convert())
        self.bind_all("<Control-KP_Enter>", lambda e: self._on_convert())
        self.bind_all("<Escape>", lambda e: self._on_cancel())

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------------- theming
    def _apply_theme(self) -> None:
        """Apply light or dark theme colors to styles and widgets."""
        style = ttk.Style(self)
        theme = self.theme_var.get()

        if theme == "Dark":
            bg = "#121212"
            card_bg = "#121212"   # flatten cards: same as window
            text_color = "#e0e0e0"
            status_info = "#64b5f6"
            status_err = "#ef5350"
            entry_bg = "#1e1e1e"
            hover_bg = "#333333"
            accent_purple = "#7b6cd9"  # Obsidian-like link color

            self.configure(bg=bg)
            style.configure("TFrame", background=bg)
            style.configure("Card.TLabelframe", background=card_bg, foreground=text_color)
            style.configure("Card.TLabelframe.Label", background=card_bg, foreground=text_color)
            style.configure("Log.TFrame", background=card_bg)

            style.configure("TLabel", background=bg, foreground=text_color)
            style.configure("Status.TLabel", background=bg, foreground=text_color)
            style.configure("StatusInfo.TLabel", background=bg, foreground=status_info)
            style.configure("StatusError.TLabel", background=bg, foreground=status_err)

            # Entries / comboboxes / spinboxes all share the same dark field
            style.configure("TEntry", fieldbackground=entry_bg, foreground=text_color)
            style.configure(
                "TCombobox",
                fieldbackground=entry_bg,
                foreground=text_color,
                background=entry_bg,
            )
            style.configure("TSpinbox", fieldbackground=entry_bg, foreground=text_color)
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
                foreground=[("readonly", text_color), ("!readonly", text_color)],
                background=[("readonly", entry_bg), ("!readonly", entry_bg)],
                selectbackground=[("readonly", accent_purple), ("!readonly", accent_purple)],
                selectforeground=[("readonly", "#ffffff"), ("!readonly", "#ffffff")],
            )
            style.map(
                "TSpinbox",
                fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
                foreground=[("readonly", text_color), ("!readonly", text_color)],
                background=[("readonly", entry_bg), ("!readonly", entry_bg)],
            )

            style.configure("TCheckbutton", background=bg, foreground=text_color)
            style.map(
                "TCheckbutton",
                background=[("active", hover_bg), ("!active", bg)],
                foreground=[("active", text_color), ("!active", text_color)],
            )

            style.configure(
                "Horizontal.TProgressbar",
                troughcolor="#1e1e1e",
                background=accent_purple,  # purple progress in dark mode
            )

            self.log_txt.configure(
                bg="#1e1e1e",
                fg=text_color,
                insertbackground=text_color,
            )
        else:
            # Light theme
            bg = "#f2f2f2"
            card_bg = "#f2f2f2"   # flatten cards
            text_color = "#000000"
            status_info = "#0066aa"
            status_err = "#b00020"
            entry_bg = "#ffffff"
            hover_bg = "#e0e0e0"
            accent_green = "#4caf50"

            self.configure(bg=bg)
            style.configure("TFrame", background=bg)
            style.configure("Card.TLabelframe", background=card_bg, foreground=text_color)
            style.configure("Card.TLabelframe.Label", background=card_bg, foreground=text_color)
            style.configure("Log.TFrame", background=card_bg)

            style.configure("TLabel", background=bg, foreground=text_color)
            style.configure("Status.TLabel", background=bg, foreground=text_color)
            style.configure("StatusInfo.TLabel", background=bg, foreground=status_info)
            style.configure("StatusError.TLabel", background=bg, foreground=status_err)

            style.configure("TEntry", fieldbackground=entry_bg, foreground=text_color)
            style.configure(
                "TCombobox",
                fieldbackground=entry_bg,
                foreground=text_color,
                background=entry_bg,
            )
            style.configure("TSpinbox", fieldbackground=entry_bg, foreground=text_color)
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
                foreground=[("readonly", text_color), ("!readonly", text_color)],
                background=[("readonly", entry_bg), ("!readonly", entry_bg)],
                selectbackground=[("readonly", "#c5cae9"), ("!readonly", "#c5cae9")],
                selectforeground=[("readonly", "#000000"), ("!readonly", "#000000")],
            )
            style.map(
                "TSpinbox",
                fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
                foreground=[("readonly", text_color), ("!readonly", text_color)],
                background=[("readonly", entry_bg), ("!readonly", entry_bg)],
            )

            style.configure("TCheckbutton", background=bg, foreground=text_color)
            style.map(
                "TCheckbutton",
                background=[("active", hover_bg), ("!active", bg)],
                foreground=[("active", text_color), ("!active", text_color)],
            )

            style.configure(
                "Horizontal.TProgressbar",
                troughcolor="#dddddd",
                background=accent_green,   # green progress in light mode
            )

            self.log_txt.configure(
                bg="#ffffff",
                fg="#000000",
                insertbackground="#000000",
            )

    # ----------------------------------------------------------------- helpers
    def _set_status(self, text: str, kind: str = "info") -> None:
        style = "StatusInfo.TLabel" if kind == "info" else "StatusError.TLabel"
        self.status_label.configure(text=text, style=style)

    def _clear_log(self) -> None:
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.configure(state="disabled")

    def _disable_open_folder_link(self) -> None:
        self.open_folder_link.configure(text="")

    def _enable_open_folder_link(self) -> None:
        self.open_folder_link.configure(text="Open folder")

    # ------------------------------------------------------------- path select
    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        self.in_path_var.set(os_display_path(path))

    def _choose_output(self) -> None:
        base = self.out_path_var.get().strip() or self.in_path_var.get().strip() or "output.md"
        initial = Path(base).name if base else "output.md"

        path = filedialog.asksaveasfilename(
            title="Save Markdown as…",
            defaultextension=".md",
            initialfile=initial,
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
        )
        if not path:
            return
        self.out_path_var.set(os_display_path(path))

    def _suggest_output(self) -> None:
        raw = self.in_path_var.get().strip()
        if not raw:
            return
        try:
            p = Path(raw)
            out = p.with_suffix(".md")
            if not self.out_path_var.get().strip():
                self.out_path_var.set(os_display_path(out))
        except Exception:
            # ignore bad paths
            pass

    # ----------------------------------------------------------- profile logic
    def _populate_profiles(self) -> None:
        names = list(BUILTIN_PROFILES.keys()) + sorted(self.custom_profiles.keys())
        if not names:
            names = ["Default"]
        self.profile_combo["values"] = names
        if self.profile_var.get() not in names:
            self.profile_var.set("Default")

    def _on_profile_selected(self, _event=None) -> None:
        name = self.profile_var.get()
        if name in BUILTIN_PROFILES:
            opts = BUILTIN_PROFILES[name]
        elif name in self.custom_profiles:
            opts = self.custom_profiles[name]
        else:
            return
        self._apply_options_dict(opts)
        self._log(f"[profile] Applied profile: {name}")

    def _save_profile_dialog(self) -> None:
        name = simpledialog.askstring("Save profile", "Profile name:", parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in BUILTIN_PROFILES:
            messagebox.showinfo(
                "Cannot overwrite built-in profile",
                f'"{name}" is a built-in profile name.\n\n'
                "Please choose a different name.",
                parent=self,
            )
            return
        if name in self.custom_profiles:
            if not messagebox.askyesno(
                "Overwrite profile?",
                f'A profile named "{name}" already exists.\n\nOverwrite it?',
                parent=self,
            ):
                return

        self.custom_profiles[name] = self._options_from_controls()
        self.profile_var.set(name)
        self._populate_profiles()
        self._save_config()
        self._log(f"[profile] Saved profile: {name}")

    def _delete_profile(self) -> None:
        name = self.profile_var.get()
        if name in BUILTIN_PROFILES:
            messagebox.showinfo(
                "Built-in profile",
                "Built-in profiles cannot be deleted.",
                parent=self,
            )
            return
        if name not in self.custom_profiles:
            messagebox.showinfo(
                "No custom profile selected",
                "Select a custom profile to delete.",
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Delete profile?",
            f'Delete custom profile "{name}"?',
            parent=self,
        ):
            return
        del self.custom_profiles[name]
        self.profile_var.set("Default")
        self._apply_options_dict(BUILTIN_PROFILES["Default"])
        self._populate_profiles()
        self._save_config()
        self._log(f"[profile] Deleted profile: {name}")

    # ----------------------------------------------------------- convert logic
    def _on_convert(self) -> None:
        # Prevent multiple concurrent runs
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo(
                "Conversion in progress",
                "A conversion is already running.\n\n"
                "Please wait for it to finish or press Stop.",
                parent=self,
            )
            return

        inp = self.in_path_var.get().strip()
        outp = self.out_path_var.get().strip()

        if not inp:
            messagebox.showwarning("Missing input PDF", "Please choose an input PDF.", parent=self)
            return
        try:
            in_path = Path(inp)
        except Exception:
            messagebox.showerror("Invalid input path", "The input path is not valid.", parent=self)
            return

        if not in_path.exists():
            messagebox.showerror("Input not found", f"Input file does not exist:\n{os_display_path(inp)}", parent=self)
            return
        if in_path.suffix.lower() != ".pdf":
            messagebox.showerror("Input is not a PDF", "The input file must have a .pdf extension.", parent=self)
            return

        if not outp:
            # auto-derive if user omitted
            outp = os_display_path(in_path.with_suffix(".md"))
            self.out_path_var.set(outp)

        self._last_output_path = outp
        self._cancel_requested = False
        self._lock_ui(busy=True)
        self._disable_open_folder_link()
        self._clear_log()
        self.pbar.configure(value=0)
        self._set_status("Converting…", kind="info")

        self._log(f"Input:  {os_display_path(inp)}")
        self._log(f"Output: {os_display_path(outp)}")
        self._log(f"OCR mode: {self.ocr_var.get()}")

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

        # Run pipeline on a background thread
        self._worker = threading.Thread(
            target=self._run_pipeline,
            args=(str(in_path), outp, opts),
            daemon=True,
        )
        self._worker.start()

    def _run_pipeline(self, inp: str, outp: str, opts: Options) -> None:
        def wrapped_progress(done: int, total: int) -> None:
            if self._cancel_requested:
                raise UserCancelled("Cancelled by user")
            self._progress_cb(done, total)

        def wrapped_log(msg: str) -> None:
            if self._cancel_requested:
                raise UserCancelled("Cancelled by user")
            self._log(msg)

        try:
            pdf_to_markdown(
                inp,
                outp,
                opts,
                progress_cb=wrapped_progress,
                log_cb=wrapped_log,
            )
        except UserCancelled:
            self._log("Cancelled by user.")
            self.after(0, lambda: self._set_status("Cancelled.", kind="info"))
            self.after(0, self._disable_open_folder_link)
        except Exception as e:
            self._log(f"Error: {e}")
            self.after(
                0,
                lambda: self._set_status("Conversion failed. See log for details.", kind="error"),
            )
            self.after(
                0,
                lambda: messagebox.showerror("Conversion failed", f"An error occurred:\n{e}", parent=self),
            )
            self.after(0, self._disable_open_folder_link)
        else:
            self._log("Done.")
            self.after(0, lambda: self._set_status("Conversion complete.", kind="info"))
            self.after(0, self._enable_open_folder_link)
        finally:
            self._cancel_requested = False
            self.after(0, lambda: self._lock_ui(busy=False))

    # -------------------------------------------------------------- callbacks
    def _log(self, msg: str) -> None:
        """Thread-safe log appender."""
        def append() -> None:
            self.log_txt.configure(state="normal")
            self.log_txt.insert("end", str(msg) + "\n")
            self.log_txt.see("end")
            self.log_txt.configure(state="disabled")

        self.after(0, append)

    def _progress_cb(self, done: int, total: int) -> None:
        try:
            pct = int((done / total) * 100) if total > 0 else 0
        except Exception:
            pct = max(0, min(100, done))
        self.after(0, lambda: self.pbar.configure(value=pct))

    def _lock_ui(self, busy: bool) -> None:
        if busy:
            self.go_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
        else:
            self.go_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")

    # -------------------------------------------------------------- cancel/quit
    def _on_cancel(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        self._cancel_requested = True
        self._set_status("Cancelling…", kind="info")
        self._log("Cancellation requested; finishing current step…")

    def _on_open_folder(self, _event=None) -> None:
        path = self._last_output_path or self.out_path_var.get().strip()
        if not path:
            return
        folder = Path(path)
        if folder.is_file():
            folder = folder.parent
        if not folder.exists():
            messagebox.showerror(
                "Folder not found",
                f"Output folder does not exist:\n{os_display_path(str(folder))}",
                parent=self,
            )
            return

        try:
            if platform.system() == "Windows":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror(
                "Could not open folder",
                f"Failed to open folder:\n{e}",
                parent=self,
            )

    def _on_close(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            if not messagebox.askyesno(
                "Quit while running?",
                "A conversion is still in progress.\n"
                "Stop it and quit?",
                parent=self,
            ):
                return
            self._cancel_requested = True
        self._save_config()
        self.destroy()

    # ---------------------------------------------------------- options helpers
    def _options_from_controls(self) -> dict:
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

    def _apply_options_dict(self, opts: dict) -> None:
        o = {**DEFAULT_OPTIONS, **opts}
        if o["ocr_mode"] not in OCR_CHOICES:
            o["ocr_mode"] = OCR_CHOICES[0]
        self.ocr_var.set(o["ocr_mode"])
        self.preview_var.set(bool(o["preview"]))
        self.export_images_var.set(bool(o["export_images"]))
        self.page_breaks_var.set(bool(o["page_breaks"]))
        self.rm_edges_var.set(bool(o["rm_edges"]))
        self.caps_to_headings_var.set(bool(o["caps_to_headings"]))
        self.defrag_var.set(bool(o["defrag"]))
        try:
            self.heading_ratio_var.set(float(o["heading_ratio"]))
        except Exception:
            self.heading_ratio_var.set(DEFAULT_OPTIONS["heading_ratio"])
        try:
            self.orphan_len_var.set(int(o["orphan_len"]))
        except Exception:
            self.orphan_len_var.set(DEFAULT_OPTIONS["orphan_len"])


if __name__ == "__main__":
    app = PdfMdApp()
    app.mainloop()
