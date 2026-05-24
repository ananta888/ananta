"""Low-level helpers for tool-output compaction. No service dependencies."""
from __future__ import annotations

import hashlib
import re
from typing import Sequence


def original_ref(text: str) -> str:
    """Return a short content-addressed ref for the raw output."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def find_preserved_lines(lines: list[str], patterns: Sequence[re.Pattern]) -> set[int]:
    """Return indices of lines matching any preserve pattern."""
    preserved: set[int] = set()
    for i, line in enumerate(lines):
        for pat in patterns:
            if pat.search(line):
                preserved.add(i)
                break
    return preserved


def build_omitted_summary(
    total_lines: int,
    head: int,
    tail: int,
    preserved_count: int,
    applied_rule_ids: list[str],
) -> str:
    kept = head + tail + preserved_count
    omitted = max(0, total_lines - kept)
    if omitted == 0:
        return ""
    return (
        f"[{omitted} line(s) omitted — "
        f"rules: {', '.join(applied_rule_ids) or 'generic_truncate'}; "
        f"{preserved_count} signal line(s) preserved]"
    )


def collapse_blank_lines(lines: list[str], max_consecutive: int = 1) -> list[str]:
    """Collapse runs of blank lines."""
    result: list[str] = []
    consecutive = 0
    for line in lines:
        if line.strip() == "":
            consecutive += 1
            if consecutive <= max_consecutive:
                result.append(line)
        else:
            consecutive = 0
            result.append(line)
    return result


def apply_keep_first_last(
    lines: list[str],
    head_lines: int,
    tail_lines: int,
    preserved_indices: set[int],
) -> tuple[list[str], list[str]]:
    """
    Return (kept_lines, omitted_line_markers).
    Lines in preserved_indices are always kept regardless of position.
    """
    n = len(lines)
    head_set = set(range(min(head_lines, n)))
    tail_set = set(range(max(0, n - tail_lines), n))
    keep_indices = head_set | tail_set | preserved_indices

    kept: list[str] = []
    omitted_markers: list[str] = []
    in_omitted_run = False

    for i, line in enumerate(lines):
        if i in keep_indices:
            if in_omitted_run:
                omitted_markers.append(f"... [omitted] ...")
                in_omitted_run = False
            kept.append(line)
        else:
            in_omitted_run = True

    return kept, omitted_markers
