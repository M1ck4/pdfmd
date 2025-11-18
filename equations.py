from __future__ import annotations

"""
Math detection and LaTeX-style normalization for pdfmd.

This module works purely on the intermediate text model defined in
models.PageText / Block / Line / Span. It does **not** depend on any
PDF geometry and intentionally avoids heavy dependencies.

Goals
-----

1. Detect lines that *behave like* mathematical content:
   - Display equations (standalone lines).
   - Inline-style math hiding inside text lines.
   - Existing LaTeX math that should be preserved.

2. Normalize math text toward LaTeX-friendly syntax:
   - Map Unicode Greek letters to \\alpha, \\beta, ...
   - Map ≤, ≥, ≠, ∞, ∑, ∫, √, ×, ·, etc. to LaTeX commands.
   - Map superscript/subscript digits (x², a₁₀) to x^{2}, a_{10}.
   - Avoid Markdown escaping here — math should be passed as-is,
     then wrapped by the renderer using `$...$` or `$$...$$`.

3. Stay conservative:
   - Prefer to miss ambiguous prose rather than misclassify it as math.
   - Treat a line as a display equation only if its "math density"
     and structure strongly suggest it.

Integration Sketch
------------------

Typical integration in `transform.py` might look like:

    from .equations import annotate_math_on_page

    for page in pages:
        annotate_math_on_page(page)

Then, in `render.py`, inside `_block_to_lines`, you can check each Line
**before** escaping Markdown:

    if getattr(line, "is_math", False):
        tex = getattr(line, "math_tex", "").strip()
        kind = getattr(line, "math_kind", "display")
        if tex:
            if kind == "display":
                md_lines.append(f"$$\\n{tex}\\n$$")
            else:
                md_lines.append(f"${tex}$")
            continue  # IMPORTANT: skip normal escaping / processing

This module intentionally stops short of that Markdown wrapping so you
can tune the behaviour per project.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import re

from .models import PageText, Block, Line


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MathDetection:
    """
    Lightweight representation of a detected math region.

    Attributes
    ----------
    block_index:
        Index of the Block within PageText.blocks.
    line_index:
        Index of the Line within block.lines.
    kind:
        Either "display" or "inline".
    raw:
        Raw (joined) line text as extracted from the PDF.
    tex:
        Normalized, LaTeX-ish math text for this line/region.
        This is **not** wrapped in `$` or `$$` — the renderer should
        decide how to wrap it.
    """
    block_index: int
    line_index: int
    kind: str  # "display" | "inline"
    raw: str
    tex: str


# ---------------------------------------------------------------------------
# Unicode → LaTeX maps
# ---------------------------------------------------------------------------

# Common Greek letters used in math.
_GREEK_MAP = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\epsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "ο": r"o",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\phi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Θ": r"\Theta",
    "Λ": r"\Lambda",
    "Ξ": r"\Xi",
    "Π": r"\Pi",
    "Σ": r"\Sigma",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
}

# Superscript and subscript digits / operators used in math.
_SUPERSCRIPT_MAP = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "⁺": "+",
    "⁻": "-",
    "⁼": "=",
    "⁽": "(",
    "⁾": ")",
}

_SUBSCRIPT_MAP = {
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
    "₊": "+",
    "₋": "-",
    "₌": "=",
    "₍": "(",
    "₎": ")",
}

# Misc Unicode math symbols to LaTeX.
_UNICODE_MATH_MAP = {
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\ne",
    "≈": r"\approx",
    "≃": r"\simeq",
    "≡": r"\equiv",
    "∞": r"\infty",
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "∮": r"\oint",
    "√": r"\sqrt",
    "∂": r"\partial",
    "∇": r"\nabla",
    "∈": r"\in",
    "∉": r"\notin",
    "⊂": r"\subset",
    "⊆": r"\subseteq",
    "⊃": r"\supset",
    "⊇": r"\supseteq",
    "⋂": r"\cap",
    "⋃": r"\cup",
    "∧": r"\wedge",
    "∨": r"\vee",
    "¬": r"\neg",
    "⇒": r"\Rightarrow",
    "→": r"\to",
    "←": r"\leftarrow",
    "⇔": r"\Leftrightarrow",
    "↦": r"\mapsto",
    "⊕": r"\oplus",
    "⊗": r"\otimes",
    "⊙": r"\odot",
    "±": r"\pm",
    "∓": r"\mp",
    "×": r"\times",
    "·": r"\cdot",
    "∝": r"\propto",
}

# Characters that make a line "mathy".
_MATH_OPERATORS = set("=<>+-*/^_")
_MATH_PARENS = set("()[]{}")


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _line_text(line: Line) -> str:
    """Join spans into a raw line string."""
    return "".join(sp.text or "" for sp in line.spans).rstrip("\n")


def _normalize_unicode_math(text: str) -> str:
    """
    Map Unicode Greek / superscripts / subscripts / math symbols to LaTeX-ish.

    We intentionally do **not** escape for Markdown here. The renderer should
    decide whether to escape or bypass escaping for math segments.

    Examples:
        "α + β²"  -> "\\alpha + \\beta^2"
        "x₁₀²"    -> "x_{10}^2"
    """
    if not text:
        return text

    out: List[str] = []
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # Greek letters
        if ch in _GREEK_MAP:
            out.append(_GREEK_MAP[ch])
            i += 1
            continue

        # Superscripts → collect contiguous run → ^{...} or ^x
        if ch in _SUPERSCRIPT_MAP:
            sup_chars: List[str] = []
            while i < n and text[i] in _SUPERSCRIPT_MAP:
                sup_chars.append(_SUPERSCRIPT_MAP[text[i]])
                i += 1
            sup_text = "".join(sup_chars)
            if len(sup_text) > 1:
                out.append(f"^{{{sup_text}}}")
            else:
                out.append("^" + sup_text)
            continue

        # Subscripts → collect contiguous run → _{...} or _x
        if ch in _SUBSCRIPT_MAP:
            sub_chars: List[str] = []
            while i < n and text[i] in _SUBSCRIPT_MAP:
                sub_chars.append(_SUBSCRIPT_MAP[text[i]])
                i += 1
            sub_text = "".join(sub_chars)
            if len(sub_text) > 1:
                out.append(f"_{{{sub_text}}}")
            else:
                out.append("_" + sub_text)
            continue

        # Generic math symbols
        if ch in _UNICODE_MATH_MAP:
            out.append(_UNICODE_MATH_MAP[ch])
            i += 1
            continue

        # Normal character
        out.append(ch)
        i += 1

    return "".join(out)


_LATEX_MATH_HINT_RE = re.compile(
    r"(\$[^$]+\$|\\\(|\\\[|\\begin\{(equation|align|gather|multline)\})"
)


def _contains_explicit_latex(text: str) -> bool:
    """Detect if the line already contains LaTeX math delimiters."""
    if "$" in text:
        return True
    if "\\(" in text or "\\)" in text:
        return True
    if "\\[" in text or "\\]" in text:
        return True
    if "\\begin{" in text and "}" in text:
        return True
    return bool(_LATEX_MATH_HINT_RE.search(text))


def _math_density(text: str) -> float:
    """
    Return a crude "mathiness" score between 0 and 1.

    We count digits, math operators, parentheses, Greek, and known
    Unicode math symbols as "math characters".
    """
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return 0.0

    math_chars = 0
    for c in non_space:
        if c.isdigit():
            math_chars += 1
            continue
        if c in _MATH_OPERATORS or c in _MATH_PARENS:
            math_chars += 1
            continue
        if c in _GREEK_MAP or c in _UNICODE_MATH_MAP:
            math_chars += 1
            continue

    return math_chars / float(len(non_space))


_EQ_OPERATOR_RE = re.compile(r"(=|≤|≥|≠|≈|≃|⇒|→|⇔|↦)")


def _looks_like_equation(text: str) -> bool:
    """
    Heuristic: does this line look like a standalone equation?

    Signals:
        - Contains an equality/comparison symbol.
        - Has reasonably high math density.
        - Not clearly a sentence (few long words, no trailing full stop).
    """
    s = text.strip()
    if not s:
        return False

    if not _EQ_OPERATOR_RE.search(s):
        # Many equations have '=', '<=', '>=', etc.
        return False

    density = _math_density(s)
    if density < 0.4:
        return False

    # Avoid obvious prose: many words + period at the end.
    words = s.split()
    if len(words) >= 7 and s.endswith("."):
        return False

    return True


def _looks_math_heavy_inline(text: str) -> bool:
    """
    Heuristic for a line that is mostly prose but contains math segments.

    We look for:
        - Non-trivial math density.
        - Presence of typical math operators or Greek letters.
    """
    s = text.strip()
    if not s:
        return False

    density = _math_density(s)
    if density < 0.25:
        return False

    if any(ch in s for ch in "=<>±×÷") or any(ch in s for ch in _GREEK_MAP.keys()):
        return True

    return False


def _split_inline_math_segments(text: str) -> List[Tuple[int, int]]:
    """
    Very lightweight segmentation into "mathy" spans inside a line.

    Returns a list of (start, end) indices for substrings that appear
    math-heavy relative to their surroundings.

    This is intentionally simple: we scan for runs containing at least
    one operator and at least one digit or Greek letter.
    """
    spans: List[Tuple[int, int]] = []
    n = len(text)
    i = 0

    while i < n:
        # Skip whitespace
        while i < n and text[i].isspace():
            i += 1
        start = i

        has_op = False
        has_digit_or_greek = False

        while i < n and not text[i].isspace():
            ch = text[i]
            if ch in _MATH_OPERATORS or ch in _UNICODE_MATH_MAP or ch in _EQ_OPERATOR_RE.pattern:
                has_op = True
            if ch.isdigit() or ch in _GREEK_MAP:
                has_digit_or_greek = True
            i += 1

        end = i
        if end > start and has_op and has_digit_or_greek:
            spans.append((start, end))

        # Move past any trailing whitespace
        while i < n and text[i].isspace():
            i += 1

    return spans


def _is_display_candidate(text: str) -> bool:
    """
    Decide whether a line should be treated as a display equation.

    A line is a display candidate if:
        - It "looks like" an equation, OR
        - It contains explicit LaTeX math and is short.
    """
    s = text.strip()
    if not s:
        return False

    if _looks_like_equation(s):
        return True

    if _contains_explicit_latex(s):
        # If the whole line is relatively short and math-heavy, prefer display.
        if len(s) <= 80 and _math_density(s) >= 0.35:
            return True

    return False


def _non_empty_line_texts(block: Block) -> List[str]:
    """Utility mirror of tables._non_empty_line_texts for reuse if needed."""
    texts: List[str] = []
    for ln in block.lines:
        t = _line_text(ln)
        if t.strip():
            texts.append(t)
    return texts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_math_text(text: str) -> str:
    """
    Normalize a math string to be more LaTeX-friendly.

    This:
        - Preserves any existing LaTeX commands.
        - Converts Unicode Greek, superscripts, subscripts, and math symbols.
        - Leaves Markdown escaping to the renderer.

    It is safe to call this on both "pure" equations and inline segments.
    """
    if not text:
        return text
    return _normalize_unicode_math(text)


def detect_math_on_page(page: PageText) -> List[MathDetection]:
    """
    Detect math-like lines on a single page.

    Detection order:
        1. Lines with explicit LaTeX math → always flagged.
        2. Lines that look like standalone equations → "display".
        3. Lines with math-heavy segments inside prose → "inline".

    For inline math, we normalize only the mathy segments the detector
    finds, leaving surrounding prose untouched.
    """
    detections: List[MathDetection] = []

    for b_idx, block in enumerate(page.blocks):
        for l_idx, line in enumerate(block.lines):
            raw = _line_text(line)
            if not raw.strip():
                continue

            # 1) Explicit LaTeX markers — trust the author and just normalize.
            if _contains_explicit_latex(raw):
                tex = convert_math_text(raw)
                detections.append(
                    MathDetection(
                        block_index=b_idx,
                        line_index=l_idx,
                        kind="display" if _is_display_candidate(raw) else "inline",
                        raw=raw,
                        tex=tex,
                    )
                )
                continue

            # 2) Standalone equation-style lines.
            if _is_display_candidate(raw):
                tex = convert_math_text(raw)
                detections.append(
                    MathDetection(
                        block_index=b_idx,
                        line_index=l_idx,
                        kind="display",
                        raw=raw,
                        tex=tex,
                    )
                )
                continue

            # 3) Prose lines with clearly math-heavy segments.
            if _looks_math_heavy_inline(raw):
                spans = _split_inline_math_segments(raw)
                if not spans:
                    # Fall back to line-level normalization.
                    tex = convert_math_text(raw)
                else:
                    # Rebuild string around normalized math segments
                    parts: List[str] = []
                    last_end = 0
                    for (start, end) in spans:
                        # Non-math text before the segment
                        parts.append(raw[last_end:start])
                        # Normalized math segment
                        parts.append(convert_math_text(raw[start:end]))
                        last_end = end
                    # Trailing non-math text
                    parts.append(raw[last_end:])
                    tex = "".join(parts)

                detections.append(
                    MathDetection(
                        block_index=b_idx,
                        line_index=l_idx,
                        kind="inline",
                        raw=raw,
                        tex=tex,
                    )
                )

    return detections


def detect_math(pages: List[PageText]) -> List[MathDetection]:
    """
    Detect math regions across all pages of a document.

    This is a thin convenience wrapper over `detect_math_on_page`.
    """
    all_detections: List[MathDetection] = []
    for page in pages:
        page_dets = detect_math_on_page(page)
        # block_index is local to each page, so no remapping needed here;
        # callers should treat detections as per-page if they care.
        all_detections.extend(page_dets)
    return all_detections


def annotate_math_on_page(page: PageText) -> List[MathDetection]:
    """
    Detect math on a page and annotate the underlying Line objects in-place.

    Side effects:
        - For each line that contains math, sets:
            line.is_math = True
            line.math_kind = "display" | "inline"
            line.math_tex  = normalized LaTeX-style text

    Returns:
        The list of MathDetection objects for further introspection if needed.
    """
    detections = detect_math_on_page(page)
    # Attach attributes directly to lines for easy use in render.py
    for det in detections:
        blk = page.blocks[det.block_index]
        if det.line_index < 0 or det.line_index >= len(blk.lines):
            continue
        ln = blk.lines[det.line_index]
        setattr(ln, "is_math", True)
        setattr(ln, "math_kind", det.kind)
        setattr(ln, "math_tex", det.tex)
    return detections


def annotate_math(pages: List[PageText]) -> List[MathDetection]:
    """
    Annotate math across all pages and return the combined detections.

    This is a convenience wrapper over `annotate_math_on_page`.
    """
    all_detections: List[MathDetection] = []
    for page in pages:
        all_detections.extend(annotate_math_on_page(page))
    return all_detections


__all__ = [
    "MathDetection",
    "convert_math_text",
    "detect_math_on_page",
    "detect_math",
    "annotate_math_on_page",
    "annotate_math",
]
