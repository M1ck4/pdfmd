from __future__ import annotations

"""Table detection and extraction for pdfmd.

This module detects and extracts tabular data from PDF text blocks, supporting
three detection strategies:

1. **Bordered tables**: Tables with explicit | or ¦ delimiters (highest priority)
2. **ASCII tables**: Tables with whitespace-separated columns (most common)
3. **Vertical tables**: Multi-block tables where each block is a row

The module uses heuristic scoring to distinguish tables from prose, lists,
and code blocks, with careful attention to:
- Column alignment consistency
- Cell content types (numeric, tokens, sentences)
- Structural patterns and density

Detection results are returned as TableDetection objects containing the
extracted grid and metadata for rendering.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple, Dict
import re

from .models import PageText, Block, Line


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TableDetection:
    """Represents a detected table region within a page.
    
    Attributes:
        block_index: Starting block index within PageText.blocks
        grid: Rectangular grid of cell strings (rows × columns)
        score: Confidence score from profiling heuristics
        n_blocks: Number of consecutive blocks this table spans
        detection_type: Method used to detect this table
    """
    block_index: int
    grid: List[List[str]]
    score: float = 0.0
    n_blocks: int = 1
    detection_type: str = "ascii"  # "bordered", "ascii", or "vertical"

    @property
    def n_rows(self) -> int:
        """Number of rows in the table."""
        return len(self.grid)

    @property
    def n_cols(self) -> int:
        """Maximum number of columns across all rows."""
        return max((len(row) for row in self.grid), default=0)


@dataclass
class GridProfile:
    """Statistical profile of a candidate table grid.
    
    Used to score and filter table candidates based on content characteristics.
    """
    n_rows: int
    n_cols: int
    non_empty_cells: int
    short_token_cells: int
    numeric_cells: int
    sentence_cells: int
    avg_len: float
    max_len: int
    header_rows: int
    score: float
    density: float = 0.0  # Fraction of non-empty cells


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_tables_on_page(page: PageText, debug: bool = False) -> List[TableDetection]:
    """Detect all tables on a single page using multiple strategies.
    
    Detection priority (highest to lowest):
    1. Bordered tables (explicit | delimiters)
    2. Vertical multi-block tables
    3. ASCII whitespace-separated tables
    
    Args:
        page: PageText object containing blocks to analyze
        debug: If True, log detection statistics to stderr
        
    Returns:
        List of TableDetection objects, sorted by block_index
    """
    detections: List[TableDetection] = []

    bordered_candidates: Dict[int, TableDetection] = {}
    ascii_candidates: Dict[int, TableDetection] = {}
    vertical_candidates: Dict[int, TableDetection] = {}

    # Strategy 1: Bordered table detection (highest confidence)
    for idx, block in enumerate(page.blocks):
        if _block_is_obviously_non_table(block):
            continue

        grid = _detect_bordered_table(block)
        if grid is None:
            continue

        prof = _profile_grid(grid)
        if not _grid_passes_profile(prof):
            continue

        bordered_candidates[idx] = TableDetection(
            block_index=idx,
            grid=grid,
            score=prof.score + 2.0,  # Bonus for explicit structure
            n_blocks=1,
            detection_type="bordered",
        )

    # Strategy 2: ASCII / single-block detection (most common case)
    for idx, block in enumerate(page.blocks):
        if idx in bordered_candidates:  # Skip if already detected as bordered
            continue
        if _block_is_obviously_non_table(block):
            continue

        grid = _detect_ascii_table_in_block(block)
        if grid is None:
            continue

        prof = _profile_grid(grid)
        if not _grid_passes_profile(prof):
            continue

        ascii_candidates[idx] = TableDetection(
            block_index=idx,
            grid=grid,
            score=prof.score,
            n_blocks=1,
            detection_type="ascii",
        )

    # Strategy 3: Vertical multi-block table detection (strict)
    n_blocks = len(page.blocks)
    start = 0
    while start < n_blocks:
        if start in bordered_candidates or start in ascii_candidates:
            start += 1
            continue
            
        run = _detect_vertical_run(page, start)
        if run is None:
            start += 1
            continue

        start_idx, end_idx, grid = run
        prof = _profile_grid(grid)
        if not _grid_passes_profile(prof):
            start = end_idx
            continue

        vertical_candidates[start_idx] = TableDetection(
            block_index=start_idx,
            grid=grid,
            score=prof.score,
            n_blocks=end_idx - start_idx,
            detection_type="vertical",
        )
        start = end_idx

    # Merge candidates with priority: bordered > vertical > ascii
    # Avoid overlapping detections
    used_blocks: set[int] = set()
    for idx in range(len(page.blocks)):
        cand: Optional[TableDetection] = None
        b = bordered_candidates.get(idx)
        v = vertical_candidates.get(idx)
        a = ascii_candidates.get(idx)

        # Priority order
        if b:
            cand = b
        elif v:
            cand = v
        elif a:
            cand = a

        if cand is None:
            continue

        # Check for conflicts with already-used blocks
        conflict = False
        for blk_idx in range(idx, idx + cand.n_blocks):
            if blk_idx in used_blocks:
                conflict = True
                break
        if conflict:
            continue

        # Mark blocks as used
        for blk_idx in range(idx, idx + cand.n_blocks):
            used_blocks.add(blk_idx)

        detections.append(cand)

    # Debug logging
    if debug:
        try:
            from .utils import log
            log(f"[tables] Page has {len(page.blocks)} blocks")
            log(f"[tables] Candidates: {len(bordered_candidates)} bordered, "
                f"{len(ascii_candidates)} ASCII, {len(vertical_candidates)} vertical")
            log(f"[tables] Final detections: {len(detections)}")
            for i, det in enumerate(detections):
                log(f"[tables]   {i+1}. {det.detection_type}: {det.n_rows}x{det.n_cols}, "
                    f"score={det.score:.2f}, blocks={det.n_blocks}")
        except ImportError:
            pass  # utils not available, skip debug output

    return detections


# ---------------------------------------------------------------------------
# Line helpers
# ---------------------------------------------------------------------------


def _line_text(line: Line) -> str:
    """Join all span texts in a line."""
    return "".join(sp.text for sp in line.spans)


def _block_line_texts(block: Block) -> List[str]:
    """Extract non-empty line texts from a block."""
    texts: List[str] = []
    for ln in block.lines:
        t = _line_text(ln)
        if t.strip():
            texts.append(t.rstrip("\n"))
    return texts


# ---------------------------------------------------------------------------
# Bordered table detection (Strategy 1)
# ---------------------------------------------------------------------------


def _detect_bordered_table(block: Block) -> Optional[List[List[str]]]:
    """Detect tables with | or ¦ delimiters (Markdown-style or plain text).
    
    Examples:
        | Name | Age | City     |
        |------|-----|----------|
        | Alice| 30  | New York |
        
        Name | Age | City
        Alice| 30  | New York
    
    Returns:
        Grid of cells if a valid bordered table is found, None otherwise.
    """
    texts = _block_line_texts(block)
    if len(texts) < 2:
        return None
    
    # Check if lines contain pipe delimiters
    pipe_lines = [t for t in texts if '|' in t or '¦' in t]
    if len(pipe_lines) < 2:
        return None
    
    # Count pipes per line to estimate consistency
    pipe_counts = [t.count('|') + t.count('¦') for t in pipe_lines]
    if not pipe_counts or max(pipe_counts) < 2:
        return None
    
    grid = []
    
    for line in pipe_lines:
        # Normalize ¦ to |
        line = line.replace('¦', '|')
        
        # Skip separator lines like |---|---| or |:---|---:|
        if re.match(r'^[\s|:\-]+$', line):
            continue
            
        # Split on pipes
        cells = [c.strip() for c in line.split('|')]
        
        # Remove empty first/last cells from leading/trailing pipes
        if cells and not cells[0]:
            cells = cells[1:]
        if cells and not cells[-1]:
            cells = cells[:-1]
            
        if cells and len(cells) >= 2:
            grid.append(cells)
    
    # Need at least 2 data rows for a valid table
    if len(grid) < 2:
        return None
    
    # Normalize to rectangular grid
    max_cols = max(len(row) for row in grid)
    normalized = []
    for row in grid:
        if len(row) < max_cols:
            row = row + [''] * (max_cols - len(row))
        normalized.append(row)
    
    return normalized


# ---------------------------------------------------------------------------
# Vertical multi-block detection (Strategy 3)
# ---------------------------------------------------------------------------


def _block_can_start_vertical(block: Block) -> bool:
    """Check if a block can be the first row of a vertical table.
    
    Vertical tables have each row as a separate block, with consistent
    line counts across blocks.
    """
    texts = _block_line_texts(block)
    n = len(texts)
    if n < 2 or n > 6:
        return False

    if any(_is_list_like_line(t) for t in texts):
        return False
    if _is_code_like_block(texts):
        return False

    avg_len = sum(len(t.strip()) for t in texts) / n
    if n <= 3 and avg_len > 80:
        return False

    return True


def _detect_vertical_run(
    page: PageText, start_idx: int
) -> Optional[Tuple[int, int, List[List[str]]]]:
    """Detect a vertical multi-block table starting at start_idx.
    
    Returns:
        Tuple of (start_idx, end_idx, grid) if valid, None otherwise.
        end_idx is exclusive (one past the last block in the table).
    """
    if start_idx >= len(page.blocks):
        return None

    first = page.blocks[start_idx]
    if not _block_can_start_vertical(first):
        return None

    first_texts = _block_line_texts(first)
    col_count = len(first_texts)
    if col_count < 2:
        return None

    blocks: List[Block] = [first]
    idx = start_idx + 1
    n_blocks = len(page.blocks)

    while idx < n_blocks:
        blk = page.blocks[idx]
        texts = _block_line_texts(blk)

        if len(texts) != col_count:
            break
        if any(_is_list_like_line(t) for t in texts):
            break
        if _is_code_like_block(texts):
            break

        blocks.append(blk)
        idx += 1

    # Need ≥3 blocks to avoid 2-block paragraph pairs
    if len(blocks) < 3:
        return None

    grid: List[List[str]] = []
    for blk in blocks:
        row = [t.strip() for t in _block_line_texts(blk)]
        if len(row) < col_count:
            row.extend('' for _ in range(col_count - len(row)))
        elif len(row) > col_count:
            row = row[:col_count]
        grid.append(row)

    if len(grid) < 2:
        return None

    return start_idx, idx, grid


# ---------------------------------------------------------------------------
# ASCII single-block detection (Strategy 2)
# ---------------------------------------------------------------------------


_CELL_SPLIT_RE_CONSERVATIVE = re.compile(r"[ \t]{3,}")
_CELL_SPLIT_RE_RELAXED = re.compile(r"[ \t]{2,}")


def _split_cells(text: str) -> List[str]:
    """Split text into cells based on whitespace.
    
    Tries 3+ spaces first (conservative), falls back to 2+ spaces.
    This helps distinguish tables from prose with occasional double spaces.
    """
    s = text.rstrip()
    if not s:
        return [""]
    
    # Try conservative split first
    cells = _CELL_SPLIT_RE_CONSERVATIVE.split(s)
    if len(cells) >= 2:
        return cells
    
    # Fall back to relaxed split
    return _CELL_SPLIT_RE_RELAXED.split(s)


def _block_is_obviously_non_table(block: Block) -> bool:
    """Quick filter to skip blocks that are clearly not tables.
    
    Checks for:
    - Too few lines
    - Short prose blocks without columns
    - High concentration of list markers
    - Lines starting with bullets
    """
    texts = _block_line_texts(block)
    if len(texts) < 2:
        return True

    # Short blocks without multi-column structure
    if len(texts) <= 3 and all(len(t.strip()) <= 40 for t in texts):
        if not any(len(_split_cells(t)) >= 2 for t in texts):
            return True

    # High concentration of list markers
    list_like = sum(1 for t in texts if _is_list_like_line(t))
    if list_like >= max(2, int(0.8 * len(texts))):
        return True
    
    # Nearly all lines start with bullets (strong list signal)
    bullet_chars = set('•◦◦-*')
    bullet_starters = sum(1 for t in texts if t.lstrip()[:1] in bullet_chars)
    if bullet_starters >= len(texts) * 0.9:
        return True

    return False


def _detect_ascii_table_in_block(block: Block) -> Optional[List[List[str]]]:
    """Detect whitespace-separated tables within a single block.
    
    Uses the most common column count as the target and normalizes rows
    to that width, merging overflow content into the last column.
    """
    texts = _block_line_texts(block)
    if len(texts) < 2:
        return None

    if _is_code_like_block(texts):
        return None

    split_lines: List[List[str]] = [_split_cells(t) for t in texts]
    is_row = [len(cells) >= 2 for cells in split_lines]
    if sum(is_row) < 2:
        return None

    # Find first and last valid table rows
    first_row = next(i for i, flag in enumerate(is_row) if flag)
    last_row = next(i for i in range(len(is_row) - 1, -1, -1) if is_row[i])

    core_lines = split_lines[first_row : last_row + 1]
    core_flags = is_row[first_row : last_row + 1]

    # Determine target column count (most common)
    row_counts = [len(cells) for cells, flag in zip(core_lines, core_flags) if flag]
    target_cols, freq = _most_common_int(row_counts)
    if target_cols < 2 or freq < max(2, int(0.6 * len(row_counts))):
        return None

    grid: List[List[str]] = []
    for cells in core_lines:
        if len(cells) < target_cols:
            # Pad short rows
            cells = cells + [''] * (target_cols - len(cells))
        elif len(cells) > target_cols:
            # Merge overflow into last column
            head = cells[: target_cols - 1]
            tail = ' '.join(cells[target_cols - 1 :]).strip()
            tail = _strip_repeated_row_tail(tail, head)
            cells = head + ([tail] if tail else [''])
        
        cleaned = [c.strip() for c in cells]
        if any(cleaned):  # Skip empty rows
            grid.append(cleaned)

    if len(grid) < 2:
        return None

    return grid


def _strip_repeated_row_tail(tail: str, head_cells: List[str]) -> str:
    """Clean up repeated content in overflow cells.
    
    Sometimes PDF extraction duplicates header text or repeats patterns
    in the tail. This function attempts to detect and remove such artifacts.
    """
    t = tail.strip()
    if not t:
        return ""
    
    # Remove if tail starts with concatenated header text
    joined_head = ' '.join(h.strip() for h in head_cells if h.strip())
    if joined_head and t.startswith(joined_head):
        rest = t[len(joined_head):].strip()
        if not rest:
            return ""
        t = rest
    
    # Detect repeated chunks (e.g., "data data data data")
    parts = t.split()
    if len(parts) >= 4:
        chunk = ' '.join(parts[: len(parts) // 2])
        if chunk and t.count(chunk) >= 3:
            return ""  # Likely a repetition artifact
    
    return t


# ---------------------------------------------------------------------------
# Grid profiling and scoring
# ---------------------------------------------------------------------------


_SENTENCE_END_RE = re.compile(r"[.!?…]+$")


def _cell_is_short_token(text: str) -> bool:
    """Check if a cell contains a short token (identifier, number, code).
    
    Short tokens are:
    - ≤24 characters
    - No internal spaces
    - Alphanumeric or numeric with punctuation
    """
    s = text.strip()
    if not s:
        return False
    if len(s) > 24:
        return False
    if ' ' in s:
        return False
    
    s_clean = s.strip('()[]{}%$€£+-')
    if not s_clean:
        return False
    
    # Pure digits or decimals
    if s_clean.isdigit():
        return True
    if s_clean.replace('.', '', 1).isdigit():
        return True
    
    # Alphanumeric identifiers
    if s_clean.isalnum():
        return True
    
    return False


def _cell_is_numeric(text: str) -> bool:
    """Check if a cell contains numeric data (including percentages)."""
    s = text.strip().replace(',', '')
    if not s:
        return False
    
    # Handle percentages and decimals
    s_clean = s.replace('.', '', 1).replace('%', '', 1)
    if s_clean.isdigit():
        return True
    
    # Handle negative numbers
    if s_clean.startswith('-') and s_clean[1:].replace('.', '', 1).isdigit():
        return True
    
    return False


def _cell_is_sentence(text: str) -> bool:
    """Check if a cell contains a complete sentence.
    
    Sentences have:
    - ≥5 words
    - Sentence-ending punctuation
    - Optional internal punctuation
    """
    s = text.strip()
    if not s:
        return False
    
    words = s.split()
    if len(words) < 5:
        return False
    
    if not _SENTENCE_END_RE.search(s):
        return False
    
    # Presence of commas/semicolons strengthens sentence signal
    if ',' in s or ';' in s:
        return True
    
    return True


def _profile_grid(grid: List[List[str]]) -> GridProfile:
    """Compute statistical profile and score for a candidate table grid.
    
    Scoring factors (positive):
    - More rows and columns
    - Higher ratio of short tokens and numeric cells
    - Consistent rectangular structure
    - Reasonable cell lengths
    
    Scoring factors (negative):
    - High ratio of sentence-like cells (suggests prose)
    - Very long average cell length
    - Low cell density
    """
    if not grid or len(grid) < 2:
        return GridProfile(0, 0, 0, 0, 0, 0, 0.0, 0, 0, 0.0, 0.0)

    n_rows = len(grid)
    n_cols = max(len(row) for row in grid)
    if n_cols < 2:
        return GridProfile(n_rows, n_cols, 0, 0, 0, 0, 0.0, 0, 0, 0.0, 0.0)

    non_empty = 0
    short_tokens = 0
    numeric = 0
    sentences = 0
    lengths: List[int] = []
    header_rows = 1  # Default assumption

    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            s = cell.strip()
            if not s:
                continue
            non_empty += 1
            L = len(s)
            lengths.append(L)
            
            if _cell_is_short_token(s):
                short_tokens += 1
            if _cell_is_numeric(s):
                numeric += 1
            if _cell_is_sentence(s):
                sentences += 1

    avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
    max_len = max(lengths) if lengths else 0
    
    # Calculate cell density
    total_cells = n_rows * n_cols
    density = non_empty / total_cells if total_cells > 0 else 0.0

    # Build score
    score = 0.0
    
    # Base score from dimensions
    score += 1.0 * n_rows
    score += 0.8 * n_cols
    
    if non_empty > 0:
        # Reward tabular content types
        score += 3.0 * (short_tokens / non_empty)
        score += 2.0 * (numeric / non_empty)
        
        # Penalize sentence-heavy content (more nuanced)
        sentence_ratio = sentences / non_empty
        if sentence_ratio > 0.8:
            score -= 4.0 * sentence_ratio
        elif sentence_ratio > 0.4:
            score -= 2.0 * sentence_ratio
    
    # Penalize very long cells (suggests paragraphs)
    if avg_len > 120:
        score -= 5.0
    
    # Bonus for substantial tables
    if n_rows >= 4 and n_cols >= 3:
        score += 2.0
    
    # Bonus for consistent column structure
    col_lengths = [len(row) for row in grid]
    if len(set(col_lengths)) == 1:  # All rows same length
        score += 1.5
    
    # Bonus for good density
    if density >= 0.6:
        score += 1.0

    return GridProfile(
        n_rows=n_rows,
        n_cols=n_cols,
        non_empty_cells=non_empty,
        short_token_cells=short_tokens,
        numeric_cells=numeric,
        sentence_cells=sentences,
        avg_len=avg_len,
        max_len=max_len,
        header_rows=header_rows,
        score=score,
        density=density,
    )


def _grid_passes_profile(prof: GridProfile) -> bool:
    """Filter grids based on profile thresholds.
    
    A grid passes if it:
    - Has sufficient dimensions (≥2x2)
    - Has non-empty content
    - Has reasonable density (≥25%)
    - Isn't too prose-heavy
    - Has adequate structural signals
    - Meets minimum score threshold
    """
    if prof.n_rows < 2 or prof.n_cols < 2:
        return False
    
    if prof.non_empty_cells == 0:
        return False
    
    # Require minimum cell density
    if prof.density < 0.25:
        return False
    
    # Sentence-heavy content check (more lenient with good structure)
    if prof.sentence_cells >= 0.6 * prof.non_empty_cells:
        # Allow if we have strong structural signals
        has_structure = (
            prof.numeric_cells > 0 or 
            prof.short_token_cells >= 0.1 * prof.non_empty_cells or
            (prof.n_rows >= 3 and prof.n_cols >= 3)
        )
        if not has_structure:
            return False
    
    # Tables should have some tokens or numbers
    if prof.short_token_cells < 0.15 * prof.non_empty_cells and prof.numeric_cells == 0:
        # More lenient for larger tables
        if prof.n_rows < 3 or prof.n_cols < 3:
            return False
    
    # Score threshold
    if prof.score < 1.0:
        return False
    
    return True


# ---------------------------------------------------------------------------
# Misc heuristics
# ---------------------------------------------------------------------------


def _is_list_like_line(text: str) -> bool:
    """Check if a line starts with a list marker.
    
    Recognized markers:
    - Bullets: -, •, ◦, ◦, *
    - Numbered: 1. or 1)
    - Lettered: A. or a)
    """
    s = text.lstrip()
    if not s:
        return False

    # Bullet markers
    if s[0] in ('-', '•', '◦', '◦', '*') and (len(s) == 1 or s[1].isspace()):
        return True

    # Numbered or lettered lists
    if re.match(r'^(\d+|[A-Za-z])(\.|\))\s+', s):
        return True

    return False


_CODE_SYMBOLS = set('{}[]();<>/=*+-')


def _is_code_like_block(lines: Iterable[str]) -> bool:
    """Check if a block looks like code rather than a table.
    
    Code indicators:
    - High density of programming symbols
    - Keywords like def, class, for, while, if
    - Type annotations (->)
    """
    texts = [ln.strip() for ln in lines if ln.strip()]
    if not texts:
        return False

    suspicious = 0
    for t in texts:
        lower = t.lower()
        
        # Programming keywords
        if lower.startswith(('def ', 'class ', 'for ', 'while ', 'if ')):
            suspicious += 1
            continue
        
        # Type annotations
        if ' -> ' in t:
            suspicious += 1
            continue

        # Symbol density
        non_space = [c for c in t if not c.isspace()]
        if not non_space:
            continue
        
        code_ratio = sum(c in _CODE_SYMBOLS for c in non_space) / float(len(non_space))
        if code_ratio >= 0.35:
            suspicious += 1

    return suspicious >= max(2, len(texts) // 2)


def _most_common_int(vals: List[int]) -> Tuple[int, int]:
    """Find the most common integer in a list.
    
    Returns:
        Tuple of (most_common_value, frequency)
    """
    if not vals:
        return 0, 0
    
    counts: Dict[int, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    
    best = max(counts, key=lambda x: counts[x])
    return best, counts[best]


__all__ = [
    "TableDetection",
    "GridProfile",
    "detect_tables_on_page",
]