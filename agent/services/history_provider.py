"""HistoryProvider Port — COSMOS-011

Commit-History, Pull Requests, Issues und ADRs als Kontext-Signale.
History ist ein ergänzendes Signal mit explizitem Freshness-Tracking,
kein primärer Wahrheitsanker.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

HISTORY_STALE_DAYS_DEFAULT = 90

# git log field / record separators (non-printable ASCII)
_GIT_FS = "\x1f"        # unit separator  — between fields
_COMMIT_MARKER = "COMMIT_MARKER"

_ADR_DIRS = ["docs/adr", "docs/decisions", "docs/ADR", "docs/Decisions"]
_ADR_EXTENSIONS = {".md", ".yaml", ".yml"}
_KNOWN_STATUSES = {"accepted", "deprecated", "proposed", "rejected"}


# ── Record dataclasses ────────────────────────────────────────────────────────

@dataclass
class CommitRecord:
    commit_id: str
    author: str
    timestamp: float         # unix timestamp
    message: str
    changed_paths: list[str]
    confidence: float = 1.0  # git history has 1.0, estimated has lower
    stale: bool = False      # True if timestamp < now - stale_threshold


@dataclass
class PRRecord:
    pr_id: str
    title: str
    author: str
    merged_at: float | None
    changed_paths: list[str]
    state: str               # "open" | "merged" | "closed"
    body_summary: str        # max 500 chars
    confidence: float = 1.0
    stale: bool = False


@dataclass
class IssueRecord:
    issue_id: str
    title: str
    author: str
    created_at: float
    state: str               # "open" | "closed"
    labels: list[str]
    body_summary: str        # max 500 chars


@dataclass
class ADRRecord:
    adr_id: str
    title: str
    status: str              # "accepted" | "deprecated" | "proposed" | "rejected"
    created_at: float | None
    summary: str             # max 500 chars
    source_path: str         # where the ADR file is


@dataclass
class HistoryProviderCapabilities:
    provider: str
    supports_git: bool
    supports_prs: bool
    supports_issues: bool
    supports_adrs: bool


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class HistoryProvider(Protocol):
    def get_commits(self, paths: list[str], limit: int = 20) -> list[CommitRecord]: ...
    def get_prs(self, paths: list[str], limit: int = 10) -> list[PRRecord]: ...
    def get_issues(self, keywords: list[str], limit: int = 10) -> list[IssueRecord]: ...
    def get_adrs(self, query: str = "") -> list[ADRRecord]: ...
    def capabilities(self) -> HistoryProviderCapabilities: ...


# ── NullHistoryProvider ───────────────────────────────────────────────────────

class NullHistoryProvider:
    """Safe default: returns empty results for all queries. No I/O."""

    def get_commits(self, paths: list[str], limit: int = 20) -> list[CommitRecord]:
        return []

    def get_prs(self, paths: list[str], limit: int = 10) -> list[PRRecord]:
        return []

    def get_issues(self, keywords: list[str], limit: int = 10) -> list[IssueRecord]:
        return []

    def get_adrs(self, query: str = "") -> list[ADRRecord]:
        return []

    def capabilities(self) -> HistoryProviderCapabilities:
        return HistoryProviderCapabilities(
            provider="null", supports_git=False, supports_prs=False,
            supports_issues=False, supports_adrs=False,
        )


# ── LocalGitHistoryProvider ───────────────────────────────────────────────────

class LocalGitHistoryProvider:
    """Reads git log via subprocess. No network calls."""

    def __init__(
        self,
        repo_path: str = ".",
        stale_days: int = HISTORY_STALE_DAYS_DEFAULT,
    ) -> None:
        self._repo_path = repo_path
        self._stale_threshold = stale_days * 86400

    # ── public interface ──────────────────────────────────────────────────────

    def get_commits(self, paths: list[str], limit: int = 20) -> list[CommitRecord]:
        """Run git log for given paths. Returns empty list on any error."""
        if not self._has_git_repo():
            return []

        fmt = f"{_COMMIT_MARKER}{_GIT_FS}%H{_GIT_FS}%an{_GIT_FS}%at{_GIT_FS}%s"
        cmd = [
            "git", "log", f"-n{limit}",
            f"--format={fmt}",
            "--name-only",
        ]
        if paths:
            cmd += ["--"] + [str(p) for p in paths]

        try:
            result = subprocess.run(
                cmd,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return []

        if result.returncode != 0:
            return []

        return self._parse_git_log(result.stdout)

    def get_prs(self, paths: list[str], limit: int = 10) -> list[PRRecord]:
        """Returns [] — local git has no PR info."""
        return []

    def get_issues(self, keywords: list[str], limit: int = 10) -> list[IssueRecord]:
        """Returns [] — local git has no issue info."""
        return []

    def get_adrs(self, query: str = "") -> list[ADRRecord]:
        """Scans for docs/adr/, docs/decisions/ YAML/Markdown files."""
        root = Path(self._repo_path)
        records: list[ADRRecord] = []

        for dir_name in _ADR_DIRS:
            adr_dir = root / dir_name
            if not adr_dir.is_dir():
                continue
            for file_path in sorted(adr_dir.iterdir()):
                if file_path.suffix.lower() not in _ADR_EXTENSIONS:
                    continue
                record = self._parse_adr_file(file_path)
                if record is not None:
                    records.append(record)

        if query:
            q = query.lower()
            records = [
                r for r in records
                if q in r.title.lower() or q in r.summary.lower()
            ]

        return records

    def capabilities(self) -> HistoryProviderCapabilities:
        return HistoryProviderCapabilities(
            provider="local_git", supports_git=True, supports_prs=False,
            supports_issues=False, supports_adrs=True,
        )

    # ── internal helpers ──────────────────────────────────────────────────────

    def _is_stale(self, timestamp: float) -> bool:
        return (time.time() - timestamp) > self._stale_threshold

    def _has_git_repo(self) -> bool:
        """Return True only if repo_path contains a valid git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_git_log(self, output: str) -> list[CommitRecord]:
        """Parse git log --format=MARKER\x1f%H\x1f%an\x1f%at\x1f%s --name-only output."""
        records: list[CommitRecord] = []
        current: dict | None = None

        for line in output.splitlines():
            if line.startswith(f"{_COMMIT_MARKER}{_GIT_FS}"):
                if current is not None:
                    records.append(self._build_commit_record(current))
                parts = line.split(_GIT_FS, 4)
                # parts: [MARKER, hash, author, unix_ts, message]
                if len(parts) >= 5:
                    try:
                        ts = float(parts[3])
                    except (ValueError, TypeError):
                        ts = 0.0
                    current = {
                        "commit_id": parts[1],
                        "author": parts[2],
                        "timestamp": ts,
                        "message": parts[4],
                        "changed_paths": [],
                    }
                else:
                    current = None
            elif line.strip() and current is not None:
                current["changed_paths"].append(line.strip())

        if current is not None:
            records.append(self._build_commit_record(current))

        return records

    def _build_commit_record(self, data: dict) -> CommitRecord:
        ts: float = data["timestamp"]
        return CommitRecord(
            commit_id=data["commit_id"],
            author=data["author"],
            timestamp=ts,
            message=data["message"],
            changed_paths=data["changed_paths"],
            confidence=1.0,
            stale=self._is_stale(ts),
        )

    def _parse_adr_file(self, file_path: Path) -> ADRRecord | None:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

        title = ""
        status = "proposed"
        created_at: float | None = None

        if file_path.suffix.lower() in {".yaml", ".yml"}:
            title, status, created_at = _parse_adr_yaml(text)
            summary = text[:500]
        else:
            title, status, created_at, summary = _parse_adr_markdown(text)

        if not title:
            title = file_path.stem

        return ADRRecord(
            adr_id=file_path.stem,
            title=title,
            status=status,
            created_at=created_at,
            summary=summary[:500],
            source_path=str(file_path),
        )


# ── ADR parsing helpers ───────────────────────────────────────────────────────

def _parse_adr_yaml(text: str) -> tuple[str, str, float | None]:
    """Simple key:value parse for ADR YAML files (avoids yaml dependency)."""
    title = ""
    status = "proposed"
    created_at: float | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("title:"):
            title = stripped[6:].strip().strip("\"'")
        elif stripped.startswith("status:"):
            s = stripped[7:].strip().strip("\"'").lower()
            if s in _KNOWN_STATUSES:
                status = s
        elif stripped.startswith("date:") or stripped.startswith("created_at:"):
            val = stripped.split(":", 1)[1].strip()
            created_at = _try_parse_date(val)

    return title, status, created_at


def _parse_adr_markdown(text: str) -> tuple[str, str, float | None, str]:
    """Parse Markdown ADR: YAML front matter + body."""
    lines = text.splitlines()
    title = ""
    status = "proposed"
    created_at: float | None = None
    in_front_matter = False
    front_matter_done = False
    body_lines: list[str] = []

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_front_matter = True
            continue

        if in_front_matter:
            if line.strip() == "---":
                in_front_matter = False
                front_matter_done = True
                continue
            stripped = line.strip()
            if stripped.startswith("title:"):
                title = stripped[6:].strip().strip("\"'")
            elif stripped.startswith("status:"):
                s = stripped[7:].strip().strip("\"'").lower()
                if s in _KNOWN_STATUSES:
                    status = s
            elif stripped.startswith("date:") or stripped.startswith("created_at:"):
                val = stripped.split(":", 1)[1].strip()
                created_at = _try_parse_date(val)
        else:
            # Pick up title from first H1 heading
            if not title and line.startswith("# "):
                title = line[2:].strip()
            # Pick up status from body line "Status: Accepted" (before front matter done)
            if not front_matter_done and line.lower().startswith("status:"):
                s = line.split(":", 1)[1].strip().lower()
                if s in _KNOWN_STATUSES:
                    status = s
            body_lines.append(line)

    # If no H1 found yet, scan body_lines
    if not title:
        for line in body_lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break

    summary = " ".join(body_lines)[:500]
    return title, status, created_at, summary


def _try_parse_date(date_str: str) -> float | None:
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


# ── mark_stale_records ────────────────────────────────────────────────────────

def mark_stale_records(
    records: list[CommitRecord | PRRecord],
    stale_days: int = HISTORY_STALE_DAYS_DEFAULT,
) -> list:
    """Returns new list with stale=True for records older than stale_days."""
    import dataclasses

    threshold = stale_days * 86400
    now = time.time()
    result = []

    for record in records:
        if isinstance(record, CommitRecord):
            ts: float | None = record.timestamp
        elif isinstance(record, PRRecord):
            ts = record.merged_at
        else:
            ts = None

        is_stale = ts is not None and (now - ts) > threshold
        result.append(dataclasses.replace(record, stale=is_stale))

    return result
