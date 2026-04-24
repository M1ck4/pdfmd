"""
Tkinter GUI for pdfmd – UI/UX with light/dark themes, profiles, and cancel support.

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

# ---------------------------------------------------------------------------
# DPI FIX – stop blurry UI on Windows when scaling > 100%
# ---------------------------------------------------------------------------
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()  # enable system DPI awareness
    except Exception:
        pass
# ---------------------------------------------------------------------------

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

OCR_CHOICES = ("off", "auto", "tesseract", "ocrmypdf")
CONFIG_PATH = Path.home() / ".pdfmd_gui.json"

# Common Tesseract language codes (user can also type a custom code).
OCR_LANG_CHOICES = (
    "eng",          # English
    "deu",          # German
    "fra",          # French
    "spa",          # Spanish
    "ita",          # Italian
    "por",          # Portuguese
    "nld",          # Dutch
    "pol",          # Polish
    "rus",          # Russian
    "chi_sim",      # Chinese (Simplified)
    "chi_tra",      # Chinese (Traditional)
    "jpn",          # Japanese
    "kor",          # Korean
    "ara",          # Arabic
    "hin",          # Hindi
    "tur",          # Turkish
    "vie",          # Vietnamese
)


DEFAULT_OPTIONS = {
    "ocr_mode": OCR_CHOICES[0],
    "ocr_lang": "eng",
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
    """Themed tooltip with Obsidian-style appearance."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 400) -> None:
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
        y += self.widget.winfo_rooty() + h + 16

        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        # Dark tooltip regardless of theme
        tip.configure(bg="#1a1a1a")
        frame = tk.Frame(
            tip, bg="#1a1a1a", padx=10, pady=6,
            highlightbackground="#444444", highlightthickness=1,
        )
        frame.pack(fill="both", expand=True)
        label = tk.Label(
            frame, text=self.text, justify="left", wraplength=340,
            bg="#1a1a1a", fg="#cccccc", font=("Segoe UI", 9),
        )
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
        self.geometry("1020x680")
        self.minsize(960, 620)

        self._worker: threading.Thread | None = None
        self._cancel_requested: bool = False
        self._last_output_path: str | None = None
        self._input_paths: list[str] = []
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

        # -- Core typography --
        _UI = "Segoe UI"
        self._fonts = {
            "body":     (_UI, 10),
            "body_sm":  (_UI, 9),
            "bold":     (_UI, 10, "bold"),
            "section":  (_UI, 11, "bold"),
            "heading":  (_UI, 13, "bold"),
            "log":      ("Consolas", 9),
            "btn":      (_UI, 10),
            "btn_accent": (_UI, 11, "bold"),
        }

        style.configure(".", font=self._fonts["body"])
        style.configure("TLabel", font=self._fonts["body"])
        style.configure("TButton", font=self._fonts["btn"], padding=(10, 5))
        style.configure("TCheckbutton", font=self._fonts["body"])

        style.configure("Status.TLabel", font=self._fonts["body_sm"])
        style.configure("StatusError.TLabel", font=self._fonts["body_sm"])
        style.configure("StatusInfo.TLabel", font=self._fonts["body_sm"])

        # Section heading style (for manual labels used as group headers)
        style.configure("Section.TLabel", font=self._fonts["section"])

        # Muted secondary text
        style.configure("Muted.TLabel", font=self._fonts["body_sm"])

        # Accent button (Convert)
        style.configure("Accent.TButton", font=self._fonts["btn_accent"], padding=(20, 8))

        # Card frames — padding provides internal breathing room
        style.configure(
            "Card.TLabelframe",
            padding=(14, 10, 14, 12),
            borderwidth=1,
            relief="flat",
        )
        style.configure("Card.TLabelframe.Label", font=self._fonts["section"])

        # Separator style for visual dividers between option groups
        style.configure("Gray.TSeparator", background="#333333")

        style.configure("Log.TFrame", padding=0)

    # ------------------------------------------------------------------- state
    def _build_vars(self) -> None:
        self.in_path_var = tk.StringVar()
        self.out_path_var = tk.StringVar()

        self.ocr_var = tk.StringVar(value=OCR_CHOICES[0])
        self.ocr_lang_var = tk.StringVar(value="eng")
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
        # Root container with generous padding
        root = ttk.Frame(self, padding=(16, 12, 16, 12))
        root.pack(fill="both", expand=True)

        # ===================== HEADER BAR ======================================
        # Profile left, theme right — full-width bar above everything
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 12))

        prof_frame = ttk.Frame(header)
        prof_frame.pack(side="left")
        ttk.Label(prof_frame, text="Profile:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.profile_combo = ttk.Combobox(
            prof_frame, textvariable=self.profile_var,
            state="readonly", width=22,
        )
        self.profile_combo.pack(side="left", padx=(0, 8))
        ttk.Button(prof_frame, text="Save\u2026", command=self._save_profile_dialog).pack(side="left", padx=(0, 4))
        ttk.Button(prof_frame, text="Delete", command=self._delete_profile).pack(side="left")

        theme_frame = ttk.Frame(header)
        theme_frame.pack(side="right")
        ttk.Label(theme_frame, text="Theme:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Combobox(
            theme_frame, values=("Dark", "Light"),
            textvariable=self.theme_var, width=7, state="readonly",
        ).pack(side="left")

        # ===================== FILES CARD ======================================
        files_card = ttk.Labelframe(root, text="\u2002Files", style="Card.TLabelframe")
        files_card.pack(fill="x", pady=(0, 10))
        files_card.columnconfigure(1, weight=1)

        ttk.Label(files_card, text="Input PDF(s):").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        in_entry = ttk.Entry(files_card, textvariable=self.in_path_var)
        in_entry.grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(files_card, text="Browse\u2026", command=self._choose_input).grid(
            row=0, column=2, sticky="e", padx=(10, 0), pady=6,
        )
        ToolTip(in_entry, "Select one or more PDFs to convert.\nAll processing is 100% local \u2014 nothing leaves your machine.")

        ttk.Label(files_card, text="Output:").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        out_entry = ttk.Entry(files_card, textvariable=self.out_path_var)
        out_entry.grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(files_card, text="Browse\u2026", command=self._choose_output).grid(
            row=1, column=2, sticky="e", padx=(10, 0), pady=6,
        )
        ToolTip(out_entry, "Output .md file (single input) or folder (batch).")

        # ===================== OPTIONS CARD ====================================
        opts_card = ttk.Labelframe(root, text="\u2002Options", style="Card.TLabelframe")
        opts_card.pack(fill="x", pady=(0, 10))

        # --- OCR settings group ---
        ocr_group = ttk.Frame(opts_card)
        ocr_group.pack(fill="x", pady=(0, 8))

        ttk.Label(ocr_group, text="OCR mode:").pack(side="left", padx=(0, 6))
        ocr_combo = ttk.Combobox(
            ocr_group, values=OCR_CHOICES,
            textvariable=self.ocr_var, width=12, state="readonly",
        )
        ocr_combo.pack(side="left", padx=(0, 20))
        ToolTip(ocr_combo,
            "off       \u2013 native text only (fastest)\n"
            "auto      \u2013 detect scanned pages, OCR when needed\n"
            "tesseract \u2013 force Tesseract on every page\n"
            "ocrmypdf  \u2013 high-fidelity OCR via OCRmyPDF",
        )

        ttk.Label(ocr_group, text="Language:").pack(side="left", padx=(0, 6))
        self.ocr_lang_combo = ttk.Combobox(
            ocr_group, values=OCR_LANG_CHOICES,
            textvariable=self.ocr_lang_var, width=10,
        )
        self.ocr_lang_combo.pack(side="left")
        ToolTip(self.ocr_lang_combo,
            "Tesseract language code for OCR.\n"
            "Select from the list or type a custom code.\n"
            "Combine with '+', e.g. 'eng+fra'.\n"
            "Only used when OCR mode is not 'off'.",
        )

        # --- Visual separator ---
        ttk.Separator(opts_card, orient="horizontal").pack(fill="x", pady=(4, 8))

        # --- Output toggles ---
        out_toggles = ttk.Frame(opts_card)
        out_toggles.pack(fill="x", pady=(0, 6))

        for text, var, pad in [
            ("Export images",              self.export_images_var, (0, 24)),
            ("Insert page breaks (\u2014\u2014\u2014)", self.page_breaks_var,   (0, 24)),
            ("Preview first 3 pages",      self.preview_var,       (0, 0)),
        ]:
            ttk.Checkbutton(out_toggles, text=text, variable=var).pack(
                side="left", padx=pad,
            )

        # --- Structure toggles ---
        struct_toggles = ttk.Frame(opts_card)
        struct_toggles.pack(fill="x", pady=(0, 6))

        for text, var, pad in [
            ("Remove repeating header / footer", self.rm_edges_var,          (0, 24)),
            ("Promote CAPS to headings",         self.caps_to_headings_var,  (0, 24)),
            ("Defragment short orphans",         self.defrag_var,            (0, 0)),
        ]:
            ttk.Checkbutton(struct_toggles, text=text, variable=var).pack(
                side="left", padx=pad,
            )

        # --- Visual separator ---
        ttk.Separator(opts_card, orient="horizontal").pack(fill="x", pady=(4, 8))

        # --- Tuning knobs ---
        tuning = ttk.Frame(opts_card)
        tuning.pack(fill="x")

        ttk.Label(tuning, text="Heading size ratio:").pack(side="left", padx=(0, 4))
        heading_spin = ttk.Spinbox(
            tuning, from_=1.0, to=2.5, increment=0.05,
            textvariable=self.heading_ratio_var, width=6,
        )
        heading_spin.pack(side="left", padx=(0, 28))
        ToolTip(heading_spin,
            "Font size \u2265 body \u00d7 this ratio \u2192 promoted to heading.\n"
            "Lower = more headings.",
        )

        ttk.Label(tuning, text="Orphan max length:").pack(side="left", padx=(0, 4))
        orphan_spin = ttk.Spinbox(
            tuning, from_=10, to=120, increment=1,
            textvariable=self.orphan_len_var, width=6,
        )
        orphan_spin.pack(side="left")
        ToolTip(orphan_spin,
            "Short isolated lines up to this many characters\n"
            "will be merged into the previous paragraph.",
        )

        # ===================== PROGRESS & LOG CARD =============================
        log_card = ttk.Labelframe(root, text="\u2002Progress & Log", style="Card.TLabelframe")
        log_card.pack(fill="both", expand=True)

        # --- Action row: Convert + Stop + Progress bar + Status ---
        action_row = ttk.Frame(log_card)
        action_row.pack(fill="x", pady=(0, 8))

        self.go_btn = ttk.Button(
            action_row, text="\u25b6  Convert",
            style="Accent.TButton", command=self._on_convert,
        )
        self.go_btn.pack(side="left", padx=(0, 8))
        ToolTip(self.go_btn, "Start conversion  (Ctrl+Enter)")

        self.stop_btn = ttk.Button(
            action_row, text="Stop", command=self._on_cancel,
        )
        self.stop_btn.pack(side="left", padx=(0, 14))
        self.stop_btn.configure(state="disabled")
        ToolTip(self.stop_btn, "Cancel  (Esc)")

        self.pbar = ttk.Progressbar(
            action_row, orient="horizontal", mode="determinate", maximum=100,
        )
        self.pbar.pack(side="left", fill="x", expand=True, padx=(0, 10))

        info_frame = ttk.Frame(action_row)
        info_frame.pack(side="right")

        self.status_label = ttk.Label(
            info_frame, text="", style="StatusInfo.TLabel", anchor="e",
        )
        self.status_label.pack(side="left", padx=(0, 6))

        self.open_folder_link = ttk.Label(
            info_frame, text="", style="StatusInfo.TLabel", cursor="hand2",
        )
        self.open_folder_link.pack(side="left")
        self.open_folder_link.bind("<Button-1>", self._on_open_folder)

        # --- Log text area with inset border ---
        log_outer = tk.Frame(
            log_card, bd=0, highlightthickness=1,
            highlightbackground="#333333",
        )
        log_outer.pack(fill="both", expand=True, pady=(0, 2))

        self.log_txt = tk.Text(
            log_outer,
            wrap="word",
            height=8,
            font=self._fonts["log"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        self.log_txt.pack(side="left", fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_outer, orient="vertical", command=self.log_txt.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_txt.configure(yscrollcommand=log_scroll.set, state="disabled")

        self._log_outer = log_outer
        self._disable_open_folder_link()

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
        """Apply Obsidian-inspired dark or clean light theme.

        Safe to call before _build_ui() completes.
        """
        if not hasattr(self, "log_txt"):
            return

        style = ttk.Style(self)
        theme = self.theme_var.get()

        if theme == "Dark":
            # -- Obsidian dark palette --
            bg           = "#1e1e1e"
            card_bg      = "#252525"
            card_border  = "#363636"
            text_color   = "#dcddde"
            text_muted   = "#888888"
            entry_bg     = "#2d2d2d"
            entry_border = "#3a3a3a"
            hover_bg     = "#383838"
            accent       = "#7b6cd9"
            accent_hover = "#6a5bb5"
            status_info  = "#7b6cd9"
            status_err   = "#e05252"
            log_bg       = "#191919"
            log_fg       = "#b0b0b0"
            sep_color    = "#333333"
            btn_bg       = "#2d2d2d"
            btn_fg       = text_color

        else:
            # -- Clean light palette --
            bg           = "#f0f0f0"
            card_bg      = "#ffffff"
            card_border  = "#d4d4d4"
            text_color   = "#1a1a1a"
            text_muted   = "#707070"
            entry_bg     = "#ffffff"
            entry_border = "#c0c0c0"
            hover_bg     = "#e4e4e4"
            accent       = "#5b5fc7"
            accent_hover = "#4a4eb5"
            status_info  = "#5b5fc7"
            status_err   = "#c62828"
            log_bg       = "#fafafa"
            log_fg       = "#333333"
            sep_color    = "#d4d4d4"
            btn_bg       = "#e0e0e0"
            btn_fg       = text_color

        # -- Window --
        self.configure(bg=bg)

        # -- Frames --
        style.configure("TFrame", background=bg)
        style.configure("Log.TFrame", background=card_bg)

        # -- Cards (labelframes) --
        style.configure(
            "Card.TLabelframe",
            background=card_bg, foreground=text_color,
            bordercolor=card_border, lightcolor=card_border, darkcolor=card_border,
        )
        style.configure("Card.TLabelframe.Label", background=card_bg, foreground=text_color)

        # -- Labels --
        style.configure("TLabel", background=bg, foreground=text_color)
        style.configure("Muted.TLabel", background=bg, foreground=text_muted)
        style.configure("Section.TLabel", background=bg, foreground=text_color)
        style.configure("Status.TLabel", background=bg, foreground=text_color)
        style.configure("StatusInfo.TLabel", background=bg, foreground=status_info)
        style.configure("StatusError.TLabel", background=bg, foreground=status_err)

        # -- Inputs --
        for widget_style in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(widget_style, fieldbackground=entry_bg, foreground=text_color, background=entry_bg)

        style.map("TCombobox",
            fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
            foreground=[("readonly", text_color), ("!readonly", text_color)],
            background=[("readonly", entry_bg), ("!readonly", entry_bg)],
            selectbackground=[("readonly", accent), ("!readonly", accent)],
            selectforeground=[("readonly", "#ffffff"), ("!readonly", "#ffffff")],
        )
        style.map("TSpinbox",
            fieldbackground=[("readonly", entry_bg), ("!readonly", entry_bg)],
            foreground=[("readonly", text_color), ("!readonly", text_color)],
            background=[("readonly", entry_bg), ("!readonly", entry_bg)],
        )

        # -- Buttons --
        style.configure("TButton", background=btn_bg, foreground=btn_fg)
        style.map("TButton",
            background=[("active", hover_bg), ("disabled", bg), ("!active", btn_bg)],
            foreground=[("disabled", text_muted)],
        )

        # -- Accent button --
        style.configure("Accent.TButton", background=accent, foreground="#ffffff")
        style.map("Accent.TButton",
            background=[("active", accent_hover), ("disabled", btn_bg), ("!active", accent)],
            foreground=[("disabled", text_muted), ("!disabled", "#ffffff")],
        )

        # -- Checkbuttons --
        # Inside cards, checkbuttons sit on card_bg; outside, on bg.
        # Clam theme uses background for the label area.
        style.configure("TCheckbutton", background=card_bg, foreground=text_color)
        style.map("TCheckbutton",
            background=[("active", hover_bg), ("!active", card_bg)],
            foreground=[("active", text_color), ("!active", text_color)],
        )

        # -- Separators --
        style.configure("TSeparator", background=sep_color)

        # -- Progress bar --
        trough = entry_bg if theme == "Dark" else "#e0e0e0"
        style.configure("Horizontal.TProgressbar", troughcolor=trough, background=accent)

        # -- Log area --
        self.log_txt.configure(bg=log_bg, fg=log_fg, insertbackground=log_fg)
        if hasattr(self, "_log_outer"):
            border_col = card_border if theme == "Dark" else "#d0d0d0"
            self._log_outer.configure(highlightbackground=border_col)

    # ----------------------------------------------------------------- helpers
    def _set_status(self, text: str, kind: str = "info") -> None:
        if not hasattr(self, "status_label"):
            return
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
        paths = filedialog.askopenfilenames(
            title="Select PDF(s)",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not paths:
            return
        # paths is a tuple of strings
        self._input_paths = [os_display_path(p) for p in paths]
        if len(self._input_paths) == 1:
            self.in_path_var.set(self._input_paths[0])
        else:
            self.in_path_var.set(f"{len(self._input_paths)} files selected")

    def _choose_output(self) -> None:
        if len(self._input_paths) > 1:
            # Multiple inputs: choose an output directory
            directory = filedialog.askdirectory(title="Choose output folder")
            if directory:
                self.out_path_var.set(os_display_path(directory))
            return

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
        # Don't auto-suggest when multiple files are selected
        if len(self._input_paths) > 1:
            if not self.out_path_var.get().strip():
                # Default to the directory of the first input
                try:
                    first_dir = str(Path(self._input_paths[0]).parent)
                    self.out_path_var.set(os_display_path(first_dir))
                except Exception:
                    pass
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

    def _resolve_input_paths(self) -> list[Path]:
        """Build the list of input PDF paths to convert.
        
        If the user picked multiple files via Browse, those are stored in
        self._input_paths. If only the text entry was edited directly (or a
        single file was picked), we fall back to the entry text.
        """
        if len(self._input_paths) > 1:
            return [Path(p) for p in self._input_paths]
        raw = self.in_path_var.get().strip()
        if raw:
            return [Path(raw)]
        return []

    @staticmethod
    def _safe_output_path(in_path: Path, out_dir: Path) -> Path:
        """Derive a .md output path that won't silently overwrite an existing file.
        
        If <stem>.md already exists in out_dir, appends _1, _2, … until a
        free name is found.
        """
        candidate = out_dir / (in_path.stem + ".md")
        if not candidate.exists():
            return candidate
        n = 1
        while True:
            candidate = out_dir / f"{in_path.stem}_{n}.md"
            if not candidate.exists():
                return candidate
            n += 1

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

        inputs = self._resolve_input_paths()
        if not inputs:
            messagebox.showwarning("Missing input PDF", "Please choose an input PDF.", parent=self)
            return

        # Validate every input
        for in_path in inputs:
            if not in_path.exists():
                messagebox.showerror(
                    "Input not found",
                    f"Input file does not exist:\n{os_display_path(str(in_path))}",
                    parent=self,
                )
                return
            if in_path.suffix.lower() != ".pdf":
                messagebox.showerror(
                    "Input is not a PDF",
                    f"Not a PDF file:\n{os_display_path(str(in_path))}",
                    parent=self,
                )
                return

        outp = self.out_path_var.get().strip()
        multiple = len(inputs) > 1

        # Build the list of (input_path, output_path) pairs
        jobs: list[tuple[Path, Path]] = []

        if multiple:
            # Output must be (or become) a directory
            if outp:
                out_dir = Path(outp)
            else:
                # Default to directory of first input
                out_dir = inputs[0].parent
                self.out_path_var.set(os_display_path(str(out_dir)))
            out_dir.mkdir(parents=True, exist_ok=True)
            for inp in inputs:
                jobs.append((inp, self._safe_output_path(inp, out_dir)))
        else:
            in_path = inputs[0]
            if not outp:
                outp = os_display_path(in_path.with_suffix(".md"))
                self.out_path_var.set(outp)
            jobs.append((in_path, Path(outp)))

        self._last_output_path = str(jobs[-1][1])

        # --- Password pre-check (only for single file) ---
        pdf_password = None
        if not multiple and fitz is not None:
            try:
                doc = fitz.open(str(inputs[0]))
                needs_pass = bool(getattr(doc, "needs_pass", False))
                if not needs_pass:
                    doc.close()
                else:
                    doc.close()
                    # Loop until user cancels or provides a correct password
                    while True:
                        pwd = simpledialog.askstring(
                            "Password required",
                            "This PDF is password protected.\n\n"
                            "Enter password to convert.\n\n"
                            "The password is used only in memory and is\n"
                            "never stored or sent anywhere.",
                            show="*",
                            parent=self,
                        )
                        if pwd is None:
                            self._set_status("Conversion cancelled (password required).", kind="info")
                            self._log("Conversion cancelled before password entry.")
                            return
                        pwd = pwd.strip()
                        if not pwd:
                            self._set_status("Conversion cancelled (empty password).", kind="info")
                            self._log("Conversion cancelled: empty password.")
                            return
                        try:
                            doc2 = fitz.open(str(inputs[0]))
                            ok = bool(doc2.authenticate(pwd))
                            doc2.close()
                        except Exception:
                            ok = False
                        if ok:
                            pdf_password = pwd
                            break
                        else:
                            messagebox.showerror(
                                "Incorrect password",
                                "The password you entered is incorrect.\n\nPlease try again.",
                                parent=self,
                            )
            except Exception:
                pdf_password = None

        # Now proceed with conversion
        self._cancel_requested = False
        self._lock_ui(busy=True)
        self._disable_open_folder_link()
        self._clear_log()
        self.pbar.configure(value=0)

        if multiple:
            self._set_status(f"Converting {len(jobs)} files…", kind="info")
        else:
            self._set_status("Converting…", kind="info")

        opts = Options(
            ocr_mode=self.ocr_var.get(),
            ocr_lang=self.ocr_lang_var.get().strip() or "eng",
            preview_only=self.preview_var.get(),
            caps_to_headings=self.caps_to_headings_var.get(),
            defragment_short=self.defrag_var.get(),
            heading_size_ratio=float(self.heading_ratio_var.get()),
            orphan_max_len=int(self.orphan_len_var.get()),
            remove_headers_footers=self.rm_edges_var.get(),
            page_break_mode="visible" if self.page_breaks_var.get() else "off",
            export_images="white" if self.export_images_var.get() else "off",
        )

        # Run pipeline on a background thread; pass password as ephemeral arg
        self._worker = threading.Thread(
            target=self._run_pipeline,
            args=(jobs, opts, pdf_password),
            daemon=True,
        )
        self._worker.start()

    def _run_pipeline(
        self,
        jobs: list[tuple[Path, Path]],
        opts: Options,
        pdf_password: str | None,
    ) -> None:
        total_jobs = len(jobs)
        successes = 0
        failures = 0

        try:
            for job_idx, (inp, outp) in enumerate(jobs):
                if self._cancel_requested:
                    self._log("Cancelled by user.")
                    self.after(0, lambda: self._set_status("Cancelled.", kind="info"))
                    self.after(0, self._disable_open_folder_link)
                    return

                if total_jobs > 1:
                    self._log(f"\n{'='*60}")
                    self._log(f"[{job_idx + 1}/{total_jobs}] {inp.name}")
                    self._log(f"{'='*60}")

                self._log(f"Input:  {os_display_path(str(inp))}")
                self._log(f"Output: {os_display_path(str(outp))}")
                self._log(f"OCR mode: {opts.ocr_mode}")

                def make_progress_cb(idx: int) -> callable:
                    def wrapped_progress(done: int, total: int) -> None:
                        if self._cancel_requested:
                            raise UserCancelled("Cancelled by user")
                        if total_jobs > 1 and total > 0:
                            # Scale progress across all jobs
                            base = int(idx * 100 / total_jobs)
                            span = 100 / total_jobs
                            pct = base + int(done * span / total)
                            self._progress_cb(pct, 100)
                        else:
                            self._progress_cb(done, total)
                    return wrapped_progress

                def wrapped_log(msg: str) -> None:
                    if self._cancel_requested:
                        raise UserCancelled("Cancelled by user")
                    self._log(msg)

                try:
                    pdf_to_markdown(
                        str(inp),
                        str(outp),
                        opts,
                        progress_cb=make_progress_cb(job_idx),
                        log_cb=wrapped_log,
                        pdf_password=pdf_password,
                    )
                    successes += 1
                except UserCancelled:
                    self._log("Cancelled by user.")
                    self.after(0, lambda: self._set_status("Cancelled.", kind="info"))
                    self.after(0, self._disable_open_folder_link)
                    return
                except Exception as e:
                    failures += 1
                    self._log(f"Error converting {inp.name}: {e}")
                    if total_jobs == 1:
                        self.after(
                            0,
                            lambda: self._set_status("Conversion failed. See log for details.", kind="error"),
                        )
                        self.after(
                            0,
                            lambda err=e: messagebox.showerror(
                                "Conversion failed", f"An error occurred:\n{err}", parent=self
                            ),
                        )
                        self.after(0, self._disable_open_folder_link)
                        return

            # All jobs done
            if total_jobs == 1:
                self._log("Done.")
                self.after(0, lambda: self._set_status("Conversion complete.", kind="info"))
                self.after(0, self._enable_open_folder_link)
            else:
                summary = f"Batch complete: {successes} succeeded"
                if failures:
                    summary += f", {failures} failed"
                self._log(f"\n{summary}.")
                kind = "info" if failures == 0 else "error"
                self.after(0, lambda: self._set_status(f"{summary}.", kind=kind))
                self.after(0, self._enable_open_folder_link)

        finally:
            self._cancel_requested = False
            pdf_password = None  # type: ignore[assignment]
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
            "ocr_lang": self.ocr_lang_var.get().strip() or "eng",
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
        self.ocr_lang_var.set(str(o.get("ocr_lang", "eng")) or "eng")
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