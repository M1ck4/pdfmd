"""Guided interactive CLI for pdfmd.

This module provides a lightweight terminal experience for users who prefer a
step-by-step workflow over passing command-line flags manually.
"""

from __future__ import annotations

import getpass
import locale
import shutil
import sys
from dataclasses import dataclass
from urllib.parse import unquote, urlparse
from pathlib import Path
from typing import Callable, Optional, Sequence

from .cli import (
    _Colors,
    _compute_stats,
    _make_colors,
    _print_stats,
)
from .extract import _HAS_PIL, _HAS_TESS, _tesseract_available
from .models import Options
from .pipeline import pdf_to_markdown


_ABORT = "__PDFMD_ABORT__"


@dataclass(frozen=True)
class _OcrLanguageChoice:
    value: str
    label_en: str
    label_zh: str


OCR_LANGUAGE_CHOICES: tuple[_OcrLanguageChoice, ...] = (
    _OcrLanguageChoice("eng", "English (eng)", "英文 (eng)"),
    _OcrLanguageChoice("chi_sim", "Simplified Chinese (chi_sim)", "简体中文 (chi_sim)"),
    _OcrLanguageChoice("chi_sim+eng", "Chinese + English (chi_sim+eng)", "中英混合 (chi_sim+eng)"),
    _OcrLanguageChoice("chi_tra", "Traditional Chinese (chi_tra)", "繁体中文 (chi_tra)"),
    _OcrLanguageChoice("chi_tra+eng", "Traditional Chinese + English (chi_tra+eng)", "繁中混合 (chi_tra+eng)"),
    _OcrLanguageChoice("jpn", "Japanese (jpn)", "日文 (jpn)"),
    _OcrLanguageChoice("jpn+eng", "Japanese + English (jpn+eng)", "日英混合 (jpn+eng)"),
    _OcrLanguageChoice("kor", "Korean (kor)", "韩文 (kor)"),
    _OcrLanguageChoice("kor+eng", "Korean + English (kor+eng)", "韩英混合 (kor+eng)"),
)

MESSAGES = {
    "en": {
        "input_closed": "Input stream closed. Exiting interactive mode.",
        "cancelled_exit": "Cancelled. Exiting interactive mode.",
        "answer_yes_no": "Please answer with 'y' or 'n'.",
        "choose_listed": "Please choose a listed number or value.",
        "enter_pdf_path": "Please enter a PDF path.",
        "help_path_intro": "Paste a local PDF path. Quoted Windows paths and drag-and-drop paths are supported.",
        "examples": "Examples:",
        "help_exit": "Type 'q' to exit interactive mode.",
        "file_not_found": "File not found: {path}",
        "folder_not_pdf": "That is a folder, not a PDF file: {path}",
        "not_a_file": "Not a file: {path}",
        "choose_pdf": "Please choose a .pdf file, not: {name}",
        "output_not_dir": "Output must be a Markdown file path, not a directory.",
        "output_should_md": "Output should end with .md",
        "ocr_mode": "OCR mode:",
        "ocr_lang": "OCR language:",
        "export_images": "Export images to an _assets folder?",
        "page_breaks": "Insert page break markers?",
        "preview_only": "Preview only the first few pages?",
        "show_stats": "Show basic Markdown stats after conversion?",
        "default_marker": " (default)",
        "choice_prompt": "Choose 1-{count} [{default_index}]: ",
        "ocr_tesseract_pkg_missing": "Tesseract OCR needs both pytesseract and Pillow. Install them with: pip install pytesseract pillow",
        "ocr_tesseract_bin_missing": "Tesseract binary was not found on PATH. Install Tesseract and restart the terminal.",
        "ocr_ocrmypdf_missing": "OCRmyPDF was not found on PATH. Install it with: pip install ocrmypdf",
        "ocr_preflight_blocked": "OCR preflight check failed. Please fix the issue above and try again.",
        "password_prompt": "PDF is password protected. Enter password (input will be hidden): ",
        "password_cancelled": "Password entry cancelled.",
        "password_missing": "No password provided; conversion cancelled.",
        "error": "Error:",
        "saved": "Saved:",
        "interactive_mode": "pdfmd interactive mode",
        "guided_intro": "Guided PDF to Markdown conversion. Type 'q' at the PDF prompt to exit.",
        "bye": "Bye.",
        "cancelled_run": "Cancelled current run.",
        "ready": "Ready to convert:",
        "label_input": "Input",
        "label_output": "Output",
        "label_ocr": "OCR",
        "label_lang": "Lang",
        "label_images": "Images",
        "label_breaks": "Breaks",
        "label_preview": "Preview",
        "yes": "yes",
        "no": "no",
        "start_now": "Start conversion now?",
        "conversion_skipped": "Conversion skipped.",
        "convert_another": "Convert another PDF?",
        "quit_prompt": "Input PDF path (or 'q' to quit)",
        "output_prompt": "Output Markdown path",
    },
    "zh": {
        "input_closed": "输入流已结束，正在退出交互模式。",
        "cancelled_exit": "已取消，正在退出交互模式。",
        "answer_yes_no": "请输入 'y' 或 'n'。",
        "choose_listed": "请输入列表里的编号或选项值。",
        "enter_pdf_path": "请输入 PDF 路径。",
        "help_path_intro": "请粘贴本地 PDF 路径。支持带引号的 Windows 路径，也支持拖拽到终端后的路径。",
        "examples": "示例：",
        "help_exit": "输入 'q' 可退出交互模式。",
        "file_not_found": "文件不存在：{path}",
        "folder_not_pdf": "这是一个文件夹，不是 PDF 文件：{path}",
        "not_a_file": "这不是一个文件：{path}",
        "choose_pdf": "请选择 .pdf 文件，而不是：{name}",
        "output_not_dir": "输出路径必须是 Markdown 文件路径，不能是目录。",
        "output_should_md": "输出文件应以 .md 结尾。",
        "ocr_mode": "OCR 模式：",
        "ocr_lang": "OCR 语言：",
        "export_images": "是否把图片导出到 _assets 文件夹？",
        "page_breaks": "是否插入分页标记？",
        "preview_only": "是否只预览前几页？",
        "show_stats": "转换完成后是否显示 Markdown 统计信息？",
        "default_marker": "（默认）",
        "choice_prompt": "请选择 1-{count} [{default_index}]：",
        "ocr_tesseract_pkg_missing": "Tesseract OCR 需要同时安装 pytesseract 和 Pillow。可执行：pip install pytesseract pillow",
        "ocr_tesseract_bin_missing": "没有在 PATH 中找到 Tesseract 可执行文件。请先安装 Tesseract，然后重新打开终端。",
        "ocr_ocrmypdf_missing": "没有在 PATH 中找到 OCRmyPDF。可执行：pip install ocrmypdf",
        "ocr_preflight_blocked": "OCR 预检查未通过。请先修复上面的依赖问题，再重新尝试。",
        "password_prompt": "PDF 已加密。请输入密码（输入内容不会显示）：",
        "password_cancelled": "已取消密码输入。",
        "password_missing": "未提供密码，本次转换已取消。",
        "error": "错误：",
        "saved": "已保存：",
        "interactive_mode": "pdfmd 交互模式",
        "guided_intro": "这是一个引导式 PDF 转 Markdown 流程。你可以在 PDF 路径提示处输入 'q' 退出。",
        "bye": "再见。",
        "cancelled_run": "已取消当前这轮转换。",
        "ready": "准备开始转换：",
        "label_input": "输入",
        "label_output": "输出",
        "label_ocr": "OCR",
        "label_lang": "语言",
        "label_images": "图片导出",
        "label_breaks": "分页",
        "label_preview": "预览",
        "yes": "是",
        "no": "否",
        "start_now": "现在开始转换吗？",
        "conversion_skipped": "已跳过本次转换。",
        "convert_another": "要继续转换另一个 PDF 吗？",
        "quit_prompt": "输入 PDF 路径（或输入 'q' 退出）",
        "output_prompt": "输出 Markdown 路径",
    },
}


def _write(line: str = "") -> None:
    print(line)


def _resolve_ui_lang(ui_lang: str) -> str:
    if ui_lang in {"en", "zh"}:
        return ui_lang
    lang = ""
    try:
        lang = locale.getdefaultlocale()[0] or ""
    except Exception:
        lang = ""
    lang = lang.lower()
    if "zh" in lang:
        return "zh"
    return "en"


def _t(lang: str, key: str, **kwargs: object) -> str:
    template = MESSAGES.get(lang, MESSAGES["en"]).get(key, MESSAGES["en"][key])
    return template.format(**kwargs)


def _safe_input(prompt: str, lang: str) -> Optional[str]:
    try:
        return input(prompt)
    except EOFError:
        _write()
        _write(_t(lang, "input_closed"))
        return None
    except KeyboardInterrupt:
        _write()
        _write(_t(lang, "cancelled_exit"))
        return None


def _prompt_text(prompt: str, lang: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw_in = _safe_input(f"{prompt}{suffix}: ", lang)
    if raw_in is None:
        return _ABORT
    raw = raw_in.strip()
    if raw:
        return raw
    return default or ""


def _normalize_path_input(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1].strip()
    if raw.lower().startswith("file:///"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path or "")
        if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
            raw = raw[1:]
        raw = raw.replace("/", "\\")
    return raw


def _prompt_yes_no(prompt: str, lang: str, default: bool = False) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        raw_in = _safe_input(f"{prompt} [{default_label}]: ", lang)
        if raw_in is None:
            return False
        raw = raw_in.strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "是"}:
            return True
        if raw in {"n", "no", "否"}:
            return False
        _write(_t(lang, "answer_yes_no"))


def _prompt_choice(prompt: str, choices: list[str], lang: str, default: str) -> str:
    default_pos = choices.index(default)
    default_index = str(default_pos + 1)

    _write(prompt)
    for pos, choice in enumerate(choices, start=1):
        marker = _t(lang, "default_marker") if choice == default else ""
        _write(f"  {pos}. {choice}{marker}")

    while True:
        raw_in = _safe_input(
            _t(lang, "choice_prompt", count=len(choices), default_index=default_index),
            lang,
        )
        if raw_in is None:
            return default
        raw = raw_in.strip().lower()
        if not raw:
            return default
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        if raw in choices:
            return raw
        _write(_t(lang, "choose_listed"))


def _prompt_input_pdf(lang: str) -> Optional[Path]:
    while True:
        raw = _prompt_text(_t(lang, "quit_prompt"), lang)
        if raw == _ABORT:
            return None
        if not raw:
            _write(_t(lang, "enter_pdf_path"))
            continue
        if raw.lower() in {"h", "help", "?"}:
            _write(_t(lang, "help_path_intro"))
            _write(_t(lang, "examples"))
            _write(r"  L:\Docs\report.pdf")
            _write(r'  "L:\Docs\report.pdf"')
            _write(r"  file:///L:/Docs/report.pdf")
            _write(_t(lang, "help_exit"))
            continue
        if raw.lower() in {"q", "quit", "exit"}:
            return None

        path = Path(_normalize_path_input(raw)).expanduser()
        if not path.exists():
            _write(_t(lang, "file_not_found", path=path))
            continue
        if not path.is_file():
            if path.is_dir():
                _write(_t(lang, "folder_not_pdf", path=path))
            else:
                _write(_t(lang, "not_a_file", path=path))
            continue
        if path.suffix.lower() != ".pdf":
            _write(_t(lang, "choose_pdf", name=path.name))
            continue
        return path


def _prompt_output_md(input_pdf: Path, lang: str) -> Path:
    default = str(input_pdf.with_suffix(".md"))
    while True:
        raw = _prompt_text(_t(lang, "output_prompt"), lang, default=default)
        if raw == _ABORT:
            raise KeyboardInterrupt
        raw = _normalize_path_input(raw)
        outp = Path(raw).expanduser()
        if outp.exists() and outp.is_dir():
            _write(_t(lang, "output_not_dir"))
            continue
        if outp.suffix.lower() != ".md":
            _write(_t(lang, "output_should_md"))
            continue
        return outp


def _ocr_language_labels(lang: str) -> list[str]:
    if lang == "zh":
        return [choice.label_zh for choice in OCR_LANGUAGE_CHOICES]
    return [choice.label_en for choice in OCR_LANGUAGE_CHOICES]


def _prompt_ocr_language(lang: str) -> str:
    labels = _ocr_language_labels(lang)
    selected_label = _prompt_choice(
        _t(lang, "ocr_lang"),
        labels,
        lang,
        default=labels[0],
    )
    selected_index = labels.index(selected_label)
    return OCR_LANGUAGE_CHOICES[selected_index].value


def _format_bool(lang: str, value: bool) -> str:
    return _t(lang, "yes") if value else _t(lang, "no")


def _build_conversion_summary(input_pdf: Path, output_md: Path, options: Options, lang: str) -> list[str]:
    lines = [
        _t(lang, "ready"),
        f"  {_t(lang, 'label_input')}:  {input_pdf}",
        f"  {_t(lang, 'label_output')}: {output_md}",
        f"  {_t(lang, 'label_ocr')}:    {options.ocr_mode}",
    ]
    if options.ocr_mode != "off":
        lines.append(f"  {_t(lang, 'label_lang')}:   {options.ocr_lang}")
    lines.extend(
        [
            f"  {_t(lang, 'label_images')}: {_format_bool(lang, options.export_images)}",
            f"  {_t(lang, 'label_breaks')}: {_format_bool(lang, options.insert_page_breaks)}",
            f"  {_t(lang, 'label_preview')}: {_format_bool(lang, options.preview_only)}",
        ]
    )
    return lines


def _build_options(lang: str) -> tuple[Options, bool]:
    ocr_mode = _prompt_choice(
        _t(lang, "ocr_mode"),
        ["off", "auto", "tesseract", "ocrmypdf"],
        lang,
        default="off",
    )

    ocr_lang = "eng"
    if ocr_mode != "off":
        ocr_lang = _prompt_ocr_language(lang)

    export_images = _prompt_yes_no(_t(lang, "export_images"), lang, default=False)
    page_breaks = _prompt_yes_no(_t(lang, "page_breaks"), lang, default=False)
    preview_only = _prompt_yes_no(_t(lang, "preview_only"), lang, default=False)
    stats = _prompt_yes_no(_t(lang, "show_stats"), lang, default=True)

    return (
        Options(
            ocr_mode=ocr_mode,
            ocr_lang=ocr_lang,
            preview_only=preview_only,
            insert_page_breaks=page_breaks,
            export_images=export_images,
        ),
        stats,
    )


def _check_ocr_dependencies(options: Options, lang: str) -> bool:
    mode = (options.ocr_mode or "off").lower()
    if mode == "off" or mode == "auto":
        return True

    if mode == "tesseract":
        ok = True
        if not (_HAS_TESS and _HAS_PIL):
            _write(_t(lang, "ocr_tesseract_pkg_missing"))
            ok = False
        if not _tesseract_available():
            _write(_t(lang, "ocr_tesseract_bin_missing"))
            ok = False
        if not ok:
            _write(_t(lang, "ocr_preflight_blocked"))
        return ok

    if mode == "ocrmypdf":
        ok = True
        if not _tesseract_available():
            _write(_t(lang, "ocr_tesseract_bin_missing"))
            ok = False
        if shutil.which("ocrmypdf") is None:
            _write(_t(lang, "ocr_ocrmypdf_missing"))
            ok = False
        if not ok:
            _write(_t(lang, "ocr_preflight_blocked"))
        return ok

    return True


def _make_progress_cb(file_label: str, colors: _Colors) -> Callable[[int, int], None]:
    def progress_cb(done: int, total: int) -> None:
        if total == 100 and 0 <= done <= 100:
            pct = int(done)
        else:
            pct = int(done * 100 / total) if total > 0 else 0
        pct = max(0, min(100, pct))

        bar_width = 24
        filled = int(bar_width * pct / 100)
        bar = "#" * filled + "." * (bar_width - filled)
        sys.stderr.write(
            f"\r{colors.info}[{bar}] {pct:3d}%  {file_label}{colors.reset}"
        )
        sys.stderr.flush()
        if pct >= 100:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return progress_cb


def _run_conversion(
    input_pdf: Path,
    output_md: Path,
    options: Options,
    show_stats: bool,
    colors: _Colors,
    lang: str,
) -> bool:
    password: Optional[str] = None
    progress_cb = _make_progress_cb(input_pdf.name, colors)

    def log_cb(message: str) -> None:
        _write(f"{colors.info}{message}{colors.reset}")

    def run_once(pdf_password: Optional[str]) -> None:
        pdf_to_markdown(
            str(input_pdf),
            str(output_md),
            options,
            progress_cb=progress_cb,
            log_cb=log_cb,
            pdf_password=pdf_password,
        )

    try:
        run_once(password)
    except Exception as exc:
        lower = str(exc).lower()
        password_keywords = [
            "password required",
            "password is required",
            "incorrect pdf password",
            "wrong password",
            "cannot decrypt",
            "encrypted",
        ]
        if any(keyword in lower for keyword in password_keywords):
            try:
                password = getpass.getpass(
                    _t(lang, "password_prompt")
                )
            except (EOFError, KeyboardInterrupt):
                _write()
                _write(f"{colors.warn}{_t(lang, 'password_cancelled')}{colors.reset}")
                return False
            if not password:
                _write(f"{colors.warn}{_t(lang, 'password_missing')}{colors.reset}")
                return False
            try:
                run_once(password)
            except Exception as retry_exc:
                _write(f"{colors.err}{_t(lang, 'error')}{colors.reset} {retry_exc}")
                return False
        else:
            _write(f"{colors.err}{_t(lang, 'error')}{colors.reset} {exc}")
            return False
    finally:
        password = None

    _write(f"{colors.ok}{_t(lang, 'saved')}{colors.reset} {output_md}")
    if show_stats:
        stats = _compute_stats(output_md)
        _print_stats(output_md, stats, colors)
    return True


def run_interactive_cli(ui_lang: str = "auto") -> int:
    lang = _resolve_ui_lang(ui_lang)
    colors = _make_colors(sys.stderr.isatty())

    _write()
    _write(f"{colors.ok}{_t(lang, 'interactive_mode')}{colors.reset}")
    _write(_t(lang, "guided_intro"))
    _write()

    while True:
        input_pdf = _prompt_input_pdf(lang)
        if input_pdf is None:
            _write(_t(lang, "bye"))
            return 0

        try:
            output_md = _prompt_output_md(input_pdf, lang)
        except KeyboardInterrupt:
            _write(_t(lang, "cancelled_run"))
            _write()
            continue
        options, show_stats = _build_options(lang)

        _write()
        for line in _build_conversion_summary(input_pdf, output_md, options, lang):
            _write(line)
        _write()

        if not _prompt_yes_no(_t(lang, "start_now"), lang, default=True):
            _write(_t(lang, "conversion_skipped"))
        else:
            if _check_ocr_dependencies(options, lang):
                _run_conversion(input_pdf, output_md, options, show_stats, colors, lang)

        _write()
        if not _prompt_yes_no(_t(lang, "convert_another"), lang, default=True):
            _write(_t(lang, "bye"))
            return 0
        _write()
