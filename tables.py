from __future__ import annotations

"""
Table detection and lightweight modeling for pdfmd.

This module works purely on the intermediate text model defined in
models.PageText / Block / Line / Span. It does **not** depend on any
PDF geometry and intentionally avoids heavy dependencies.

Goal:
    - Detect blocks that *behave like* simple text tables
      (reports, CSV-like grids, schedules).
    - Extract a rectangular grid of cell strings per table.

It is deliberately conservative: we prefer to miss a messy table rather
than misclassify normal paragraphs, lists, or code blocks as tables.

This version supports two complementary detection strategies:

    1. Classic text-table detection based on 2+ spaces / tab separators.
    2. Vertical “grid-of-blocks” detection, where each Block contains
       N short lines (N >= 2) and several adjacent Blocks share the
       same N, forming rows like:

           Block 3:  Name
                     Role
                     Notes

           Block 4:  Alice
                     Analyst
                     Loves clean Markdown

       which becomes a 4x3 grid when combined.
"""

from dataclasses import dataclass
from typing import List, Iterable, Optional
import re

from .models import PageText, Block, Line


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TableDetection:
    """
    Lightweight representation of a detected table.

    Attributes
    ----------
    block_index:
        Index of the Block within PageText.blocks that is interpreted
        as a table. For multi-block vertical tables, this is the index
        of the **first** block in the run.
    n_rows, n_cols:
        Dimensions of the table grid.
    grid:
        Text content of the table as a rectangular list-of-lists of
        strings. `grid[r][c]` is the cell at row r, column c.
    """
    block_index: int
    n_rows: int
    n_cols: int
    grid: List[List[str]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_tables_on_page(page: PageText) -> List[TableDetection]:
    """
    Detect table-like regions on a single page.

    This performs, in order:

        1. A vertical multi-block scan:

           Attempts to find *runs* of adjacent blocks that each contain
           the same small number of non-empty lines (2–6) and look like
           structured rows (not lists or code). Each block becomes a row
           and each line a cell. This is intended for PDFs like:

               Name       (block 0, 3 lines)
               Role
               Notes

               Alice      (block 1, 3 lines)
               Analyst
               Loves clean Markdown

           which PyMuPDF tends to split into separate blocks.

        2. Classic per-block text-table detection:

           For each remaining block, run the older heuristics:

               - Heuristic pre-filtering to find candidate blocks that
                 *might* be tables.
               - Column segmentation using runs of 2+ spaces and/or tab
                 characters.
               - Rectangular grid construction for each accepted table.

    It is conservative by design. A region is only accepted as a table
    if multiple rows share a similar structure.
    """
    detections: List[TableDetection] = []
    consumed_blocks: set[int] = set()

    # ------------------------------------------------------------------
    # 1) Vertical “grid-of-blocks” detection
    # ------------------------------------------------------------------
    idx = 0
    n_blocks = len(page.blocks)

    while idx < n_blocks:
        if idx in consumed_blocks:
            idx += 1
            continue

        vertical = _detect_vertical_table_from_blocks(page, idx)
        if vertical is not None:
            grid = vertical["grid"]
            n_rows = len(grid)
            n_cols = len(grid[0]) if n_rows else 0

            if n_rows >= 2 and n_cols >= 2:
                detections.append(
                    TableDetection(
                        block_index=vertical["block_index"],
                        n_rows=n_rows,
                        n_cols=n_cols,
                        grid=grid,
                    )
                )
                # Mark all participating blocks as consumed so we do not
                # try to reinterpret them as separate tables.
                start = vertical["block_index"]
                for b_idx in range(start, start + vertical["n_blocks"]):
                    consumed_blocks.add(b_idx)
                idx = start + vertical["n_blocks"]
                continue

        idx += 1

    # ------------------------------------------------------------------
    # 2) Classic single-block text-table detection
    # ------------------------------------------------------------------
    for idx, block in enumerate(page.blocks):
        if idx in consumed_blocks:
            # Already part of a vertical table.
            continue

        if _block_is_obviously_non_table(block):
            continue

        if not _is_candidate_table_block(block):
            continue

        grid = _build_table_grid(block)
        if grid is None:
            continue

        n_rows = len(grid)
        n_cols = len(grid[0]) if n_rows else 0
        if n_rows >= 2 and n_cols >= 2:
            detections.append(
                TableDetection(
                    block_index=idx,
                    n_rows=n_rows,
                    n_cols=n_cols,
                    grid=grid,
                )
            )

    return detections


# ---------------------------------------------------------------------------
# Core heuristics
# ---------------------------------------------------------------------------


def _line_text(line: Line) -> str:
    return "".join(sp.text for sp in line.spans)


def _non_empty_line_texts(block: Block) -> List[str]:
    texts: List[str] = []
    for ln in block.lines:
        t = _line_text(ln).rstrip("\n")
        if t.strip():
            texts.append(t)
    return texts


# --- Vertical multi-block detection ----------------------------------------


def _block_can_start_vertical(block: Block) -> bool:
    """
    Lightweight check for whether a Block could be the first row in a
    vertical table (each line = cell, each Block = row).

    We intentionally keep this narrow to avoid false positives:
        - Between 2 and 6 non-empty lines.
        - Not obviously a list.
        - Not obviously code.
        - Not just a tiny 2–3 line paragraph of long text.
    """
    texts = _non_empty_line_texts(block)
    if len(texts) < 2 or len(texts) > 6:
        return False

    # Avoid list-like/opening bullets as table starts.
    if any(_is_list_like_line(t) for t in texts):
        return False

    # Avoid code-like regions.
    if _is_code_like_block(texts):
        return False

    # Short blocks where every line is very long are likely paragraphs.
    if len(texts) <= 3 and all(len(t.strip()) > 80 for t in texts):
        return False

    return True


def _detect_vertical_table_from_blocks(
    page: PageText, start_idx: int
) -> Optional[dict]:
    """
    Try to interpret a *run* of adjacent blocks as a table where each
    Block contributes one row and each non-empty line within a Block is a
    cell.

    Example (three-column vertical table):

        Block i:
            "Name"
            "Role"
            "Notes"

        Block i+1:
            "Alice"
            "Analyst"
            "Loves clean Markdown"

        Block i+2:
            "Bob"
            "Engineer"
            "Hates messy line wraps"

    We require:
        - The first block passes _block_can_start_vertical().
        - Each subsequent block in the run has the same number of
          non-empty lines.
        - No block in the run is list-like or heavily code-like.
        - At least 2 blocks in the run (>= 2 rows).
    """
    if start_idx >= len(page.blocks):
        return None

    first = page.blocks[start_idx]
    if not _block_can_start_vertical(first):
        return None

    first_texts = _non_empty_line_texts(first)
    col_count = len(first_texts)
    if col_count < 2:
        # A single-column grid isn't very helpful as a table.
        return None

    blocks: List[Block] = [first]
    idx = start_idx + 1
    n_blocks = len(page.blocks)

    while idx < n_blocks:
        b = page.blocks[idx]
        texts = _non_empty_line_texts(b)

        # Must match the same "number of columns" (lines).
        if len(texts) != col_count:
            break

        # Do not merge blocks that look like lists or code.
        if any(_is_list_like_line(t) for t in texts):
            break
        if _is_code_like_block(texts):
            break

        blocks.append(b)
        idx += 1

    if len(blocks) < 2:
        # Need at least two rows to be worth calling a table.
        return None

    # Build the grid: each block -> one row, each line -> one cell.
    grid: List[List[str]] = []
    for b in blocks:
        row_texts = [t.strip() for t in _non_empty_line_texts(b)]
        # Extra safety: pad if something changed mid-run.
        if len(row_texts) < col_count:
            row_texts.extend([""] * (col_count - len(row_texts)))
        grid.append(row_texts)

    # Basic sanity check: we still require at least a 2x2 grid.
    if len(grid) < 2 or col_count < 2:
        return None

    return {
        "block_index": start_idx,
        "n_blocks": len(blocks),
        "n_rows": len(grid),
        "n_cols": col_count,
        "grid": grid,
    }


# --- Classic single-block heuristics ---------------------------------------


def _block_is_obviously_non_table(block: Block) -> bool:
    """
    Cheap early exits: single short line, clearly a heading, etc.
    """
    texts = _non_empty_line_texts(block)
    if len(texts) < 2:
        return True

    # Very short lines (e.g. heading + short subtitle) rarely form tables.
    if len(texts) <= 3 and all(len(t.strip()) <= 40 for t in texts):
        # Avoid rejecting genuine 2x2 tiny tables, so require at least some
        # obvious column separators to keep it as a candidate.
        if not any(_count_potential_cells(t) >= 2 for t in texts):
            return True

    return False


def _is_candidate_table_block(block: Block) -> bool:
    """
    Decide whether a Block is worth attempting to interpret as a table.

    The heuristic tries to avoid false positives on:

        * Bullet / numbered lists
        * Markdown-style lists
        * Code-like blocks

    and only accepts blocks where **most** lines appear to have at least
    two "cells" separated by 2+ spaces or tab characters, with a fairly
    consistent column count.
    """
    texts = _non_empty_line_texts(block)
    if len(texts) < 2:
        return False

    # Reject blocks that are overwhelmingly list-like.
    list_like_count = sum(1 for t in texts if _is_list_like_line(t))
    if list_like_count >= max(2, int(0.6 * len(texts))):
        return False

    # Reject blocks that look like code (lots of braces, operators, etc).
    if _is_code_like_block(texts):
        return False

    # Count "cells" per line based on 2+ spaces or tabs.
    cell_counts: List[int] = []
    for t in texts:
        cells = _split_into_cells(t)
        if len(cells) >= 2:
            cell_counts.append(len(cells))

    if len(cell_counts) < 2:
        # Not enough lines with 2+ cells to form a table.
        return False

    # Require a dominant column count across lines.
    most_common_cols, freq = _most_common_int(cell_counts)
    if most_common_cols < 2:
        return False

    # At least 60 percent of lines with cells should share the same count.
    if freq < max(2, int(0.6 * len(cell_counts))):
        return False

    return True


def _build_table_grid(block: Block) -> Optional[List[List[str]]]:
    """
    Attempt to construct a rectangular cell grid for a candidate table block.

    The algorithm:

        * Extract non-empty lines.
        * Split each by 2+ spaces / tabs into cells.
        * Determine the most common column count.
        * For lines with that count, keep their cells.
        * For other lines, lightly normalize by padding/truncating.
    """
    texts = _non_empty_line_texts(block)
    if len(texts) < 2:
        return None

    split_lines: List[List[str]] = []
    for t in texts:
        cells = _split_into_cells(t)
        if any(c.strip() for c in cells):
            split_lines.append(cells)

    if len(split_lines) < 2:
        return None

    counts = [len(c) for c in split_lines if len(c) >= 1]
    if not counts:
        return None

    target_cols, freq = _most_common_int(counts)
    if target_cols < 2:
        return None

    # Build normalized grid.
    grid: List[List[str]] = []
    for cells in split_lines:
        if not any(c.strip() for c in cells):
            continue
        # Pad or trim to target_cols.
        if len(cells) < target_cols:
            cells = cells + [""] * (target_cols - len(cells))
        elif len(cells) > target_cols:
            # Merge overflow cells into the last one.
            head = cells[: target_cols - 1]
            tail = " ".join(cells[target_cols - 1 :]).strip()
            cells = head + [tail]
        # Clean cell content.
        cleaned = [c.strip() for c in cells]
        grid.append(cleaned)

    # Require at least 2 rows after normalization.
    if len(grid) < 2:
        return None

    return grid


# ---------------------------------------------------------------------------
# Helper heuristics
# ---------------------------------------------------------------------------


_CELL_SPLIT_RE = re.compile(r"[ \t]{2,}")


def _split_into_cells(text: str) -> List[str]:
    """
    Split a line into candidate cells based on runs of 2+ spaces or tabs.

    This is intentionally simple and layout-agnostic. It works well for
    monospace or visually aligned tabular text where columns are separated
    by multiple spaces.
    """
    stripped = text.rstrip()
    if not stripped:
        return [""]
    return _CELL_SPLIT_RE.split(stripped)


def _count_potential_cells(text: str) -> int:
    return len(_split_into_cells(text))


def _is_list_like_line(text: str) -> bool:
    """
    Detect common list syntaxes to avoid misclassifying them as tables.

    Examples:
        - "• Item one"
        - "- Bullet"
        - "1. First"
        - "a) Lettered"
    """
    s = text.lstrip()
    if not s:
        return False

    # Bullet-like markers.
    if s[0] in ("-", "•", "○", "◦", "*"):
        if len(s) == 1 or s[1].isspace():
            return True

    # Numbered or lettered list: "1. ", "2) ", "a. ", "B) "
    if re.match(r"^(\d+|[A-Za-z])(\.|\))\s+", s):
        return True

    return False


_CODE_SYMBOLS = set("{}[]();<>/=*+-")


def _is_code_like_block(lines: Iterable[str]) -> bool:
    """
    Heuristic detection for code-like blocks.

    We don't try to be perfect here; the goal is simply to reject very
    code-heavy regions that would otherwise be misread as tables.
    """
    texts = [ln.strip() for ln in lines if ln.strip()]
    if not texts:
        return False

    suspicious = 0
    for t in texts:
        # Fast path: explicit hints like "def ", "class ", "public", etc.
        lowered = t.lower()
        if (
            lowered.startswith("def ")
            or lowered.startswith("class ")
            or " -> " in t
            or lowered.startswith("for ")
            or lowered.startswith("while ")
            or lowered.startswith("if ")
        ):
            suspicious += 1
            continue

        # Look at symbol density.
        non_space = [c for c in t if not c.isspace()]
        if not non_space:
            continue
        codeish = sum(c in _CODE_SYMBOLS for c in non_space) / float(len(non_space))
        if codeish >= 0.35:
            suspicious += 1

    if not texts:
        return False

    # If at least half the lines look code-like, treat the block as code.
    return suspicious >= max(2, len(texts) // 2)


def _most_common_int(vals: List[int]) -> tuple[int, int]:
    """
    Return (value, frequency) for the most common integer in vals.
    """
    if not vals:
        return 0, 0
    counts: dict[int, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    best_val = max(counts, key=lambda x: counts[x])
    return best_val, counts[best_val]
