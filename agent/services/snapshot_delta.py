"""SnapshotDeltaService — diffs two compact DOM snapshots produced by the
Angular UiSnapshotService and yields human-readable change lines for the
visual snake log.

This is a pure-Python module so it can be unit-tested directly and used
by both the backend (which persists deltas in the ananta-visual session)
and any future Python consumer. The TypeScript UiSnapshotService
mirrors the format on the client side.

Snapshot format reminder (see ui-snapshot.service.ts):

    /teams | nav:Dashboard|Chats|Teams* | tab:Blueprints*|Mitglieder |
    h:Teams & Blueprints | list:3 | focus:input[Name]="My Team" |
    err:Fehler XY

Marker rules:
    * = active / selected / current
    [disabled] = disabled control
    [open] = open dialog/dropdown
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


# ── Public dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotDelta:
    """The structured diff between two compact DOM snapshots."""
    lines: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.lines and not self.changed_paths

    def as_compact_text(self, *, max_chars: int = 400) -> str:
        """Render as a single line for logging / persistence."""
        if self.is_empty():
            return "(no change)"
        joined = " | ".join(self.lines)
        if len(joined) > max_chars:
            return joined[: max_chars - 1] + "…"
        return joined


# ── Section extraction helpers ──────────────────────────────────────────────


_PATH_RE = re.compile(r"^(\/[^\s|]*)")
_LIST_RE = re.compile(r"\blist:(\d+)\b")
_FOCUS_RE = re.compile(
    r'focus:(?P<tag>\w+)\[(?P<label>[^\]]*)\]=\"(?P<value>[^\"]*)\"'
)
_HEADING_RE = re.compile(r"\bh:([^|]+?)(?=\s*\||\s*$)")
_ERROR_RE = re.compile(r"\berr:([^|]+?)(?=\s*\||\s*$)")


def extract_path(snapshot: str) -> str:
    """Return the first `/...` token in the snapshot (the current route)."""
    if not snapshot:
        return ""
    m = _PATH_RE.match(snapshot.strip())
    return m.group(1) if m else ""


def extract_list_count(snapshot: str) -> int:
    """Return the value of the `list:N` marker (0 if absent)."""
    if not snapshot:
        return 0
    m = _LIST_RE.search(snapshot)
    return int(m.group(1)) if m else 0


def extract_focus(snapshot: str) -> dict[str, str] | None:
    """Return the `focus:...` marker contents, or None when no input is focused."""
    if not snapshot:
        return None
    m = _FOCUS_RE.search(snapshot)
    if not m:
        return None
    return {
        "tag": m.group("tag"),
        "label": m.group("label") or "",
        "value": m.group("value") or "",
    }


def _extract_segment(snapshot: str, marker: str) -> str:
    """Return the text of a single section identified by its `marker:` prefix.

    Used internally for heading / error / tab / nav comparisons. Returns the
    raw segment text (without the marker prefix), or empty string when the
    section is missing."""
    if not snapshot or marker not in snapshot:
        return ""
    # Find the marker at a word boundary
    pattern = re.compile(rf"(?:^|\s| \|){re.escape(marker)}([^\|]*)")
    m = pattern.search(snapshot)
    if not m:
        return ""
    return m.group(1).strip()


# ── The core diff ───────────────────────────────────────────────────────────


# Sections the diff watches. Each entry: (marker, formatter).
# Formatter receives (prev_segment, curr_segment) and returns either "" (no
# change) or a one-line "section: prev → curr" description.
_SECTION_DIFFS: list[tuple[str, "callable"]] = []


def _register_diff(marker: str):
    def _wrap(fn):
        _SECTION_DIFFS.append((marker, fn))
        return fn
    return _wrap


@_register_diff("h:")
def _diff_heading(prev_seg: str, curr_seg: str) -> str:
    if not prev_seg and not curr_seg:
        return ""
    if prev_seg == curr_seg:
        return ""
    return f"h: {prev_seg} → {curr_seg}".strip(" →")


@_register_diff("err:")
def _diff_error(prev_seg: str, curr_seg: str) -> str:
    if prev_seg == curr_seg:
        return ""
    if curr_seg and not prev_seg:
        return f"err: new → {curr_seg}"
    if prev_seg and not curr_seg:
        return f"err: {prev_seg} → cleared"
    return f"err: {prev_seg} → {curr_seg}"


@_register_diff("tab:")
def _diff_tab(prev_seg: str, curr_seg: str) -> str:
    if prev_seg == curr_seg:
        return ""
    if not prev_seg and curr_seg:
        return f"tab: (none) → {curr_seg}"
    if prev_seg and not curr_seg:
        return f"tab: {prev_seg} → (none)"
    return f"tab: {prev_seg} → {curr_seg}"


@_register_diff("nav:")
def _diff_nav(prev_seg: str, curr_seg: str) -> str:
    if prev_seg == curr_seg:
        return ""
    return f"nav: {prev_seg or '(none)'} → {curr_seg or '(none)'}"


@_register_diff("dlg:")
def _diff_dialog(prev_seg: str, curr_seg: str) -> str:
    if prev_seg == curr_seg:
        return ""
    if curr_seg and not prev_seg:
        return f"dlg: opened → {curr_seg}"
    if prev_seg and not curr_seg:
        return f"dlg: {prev_seg} → closed"
    return f"dlg: {prev_seg} → {curr_seg}"


def _diff_list_count(prev_snap: str, curr_snap: str) -> str:
    p = extract_list_count(prev_snap)
    c = extract_list_count(curr_snap)
    if p == c:
        return ""
    return f"list: {p} → {c}"


def _diff_focus(prev_snap: str, curr_snap: str) -> str:
    p = extract_focus(prev_snap)
    c = extract_focus(curr_snap)
    if p == c:
        return ""
    if not p and c:
        return f"focus: → {c['tag']}[{c['label']}]=\"{c['value']}\""
    if p and not c:
        return f"focus: {p['tag']}[{p['label']}] → cleared"
    return f"focus: {p['tag']}[{p['label']}]=\"{p['value']}\" → {c['tag']}[{c['label']}]=\"{c['value']}\""


def diff_snapshots(prev: str, curr: str) -> SnapshotDelta:
    """Compute the human-readable delta between two compact DOM snapshots.

    An empty `prev` is treated as 'no baseline yet' → returns an empty
    delta.  The first tick after a page load is a baseline, not a change;
    the visual log will still persist the raw [ui-tick] text so the
    user sees the initial state."""
    if not prev or not curr:
        return SnapshotDelta()
    if prev == curr:
        return SnapshotDelta()

    lines: list[str] = []
    changed_paths: list[str] = []

    # 1) Path change is the strongest signal — surface it explicitly.
    p_path = extract_path(prev)
    c_path = extract_path(curr)
    if p_path != c_path:
        changed_paths.append(f"{p_path} → {c_path}")
        lines.append(f"path: {p_path or '(none)'} → {c_path or '(none)'}")

    # 2) Per-section diffs (h:, err:, tab:, nav:, dlg:).
    for marker, fn in _SECTION_DIFFS:
        prev_seg = _extract_segment(prev, marker)
        curr_seg = _extract_segment(curr, marker)
        out = fn(prev_seg, curr_seg)
        if out:
            lines.append(out)

    # 3) list: count change.
    list_line = _diff_list_count(prev, curr)
    if list_line:
        lines.append(list_line)

    # 4) focus: change.
    focus_line = _diff_focus(prev, curr)
    if focus_line:
        lines.append(focus_line)

    return SnapshotDelta(lines=lines, changed_paths=changed_paths)
