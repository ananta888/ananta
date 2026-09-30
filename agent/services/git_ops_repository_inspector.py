"""Low-level Git repository inspection for the Git Ops read models.

``GitRepositoryInspector`` turns raw ``git`` output (porcelain status,
numstat, reflog, log lines) into Ops value objects. It knows nothing about
workspaces, policy or audit (SRP); the read service composes it.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.services.git_ops_workspace_access import CONFLICT_STATES, GitCommandExecutor, literal_pathspec
from agent.services.ops_command_runner import CommandResult
from agent.services.ops_models import GitActivityEvent, GitChangedFile, GitCommitSummary, GitDiffStat
from agent.services.ops_registry_service import WorkspaceRef

_MAX_CHANGED_FILES = 1000
_MAX_UNTRACKED_DIFF_CHARS = 64_000


class GitRepositoryInspector:
    """Parses repository state from hardened ``git`` invocations."""

    def __init__(self, git: GitCommandExecutor) -> None:
        self._git = git

    def changed_files(self, cwd: Path) -> tuple[list[GitChangedFile], bool]:
        result = self._git.run(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=cwd)
        if result.returncode != 0:
            return [], result.truncated
        staged_stats = {item.path: item for item in self.diff_stats(cwd, "staged", "")}
        unstaged_stats = {item.path: item for item in self.diff_stats(cwd, "unstaged", "")}
        files: list[GitChangedFile] = []
        records = result.stdout.split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if len(record) < 3:
                continue
            x, y = record[0], record[1]
            path = record[3:]
            original_path = ""
            if (x in {"R", "C"} or y in {"R", "C"}) and index < len(records):
                original_path = records[index]
                index += 1
            untracked = x == "?" and y == "?"
            staged = not untracked and x not in {" ", "?"}
            unstaged = not untracked and y not in {" ", "?"}
            conflicted = f"{x}{y}" in CONFLICT_STATES
            left = staged_stats.get(path)
            right = unstaged_stats.get(path)
            additions = (left.additions if left else 0) + (right.additions if right else 0)
            deletions = (left.deletions if left else 0) + (right.deletions if right else 0)
            if untracked:
                additions = self.line_count(cwd / path, root=cwd)
            files.append(
                GitChangedFile(
                    path=path,
                    original_path=original_path,
                    index_status="?" if untracked else ("" if x == " " else x),
                    worktree_status="?" if untracked else ("" if y == " " else y),
                    staged=staged,
                    unstaged=unstaged,
                    untracked=untracked,
                    conflicted=conflicted,
                    renamed=x == "R" or y == "R",
                    deleted=x == "D" or y == "D",
                    binary=bool((left and left.binary) or (right and right.binary)),
                    additions=additions,
                    deletions=deletions,
                )
            )
        return files[:_MAX_CHANGED_FILES], result.truncated or len(files) > _MAX_CHANGED_FILES

    def diff_command(self, cwd: Path, scope: str, path: str) -> CommandResult:
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--find-renames"]
        if scope == "staged":
            args.extend(["--cached", "HEAD"])
        elif scope == "combined":
            args.append("HEAD")
        if path:
            args.extend(["--", literal_pathspec(path)])
        return self._git.run(args, cwd=cwd, timeout_seconds=15)

    def diff_stats(self, cwd: Path, scope: str, path: str) -> list[GitDiffStat]:
        args = ["diff", "--numstat"]
        if scope == "staged":
            args.extend(["--cached", "HEAD"])
        elif scope == "combined":
            args.append("HEAD")
        if path:
            args.extend(["--", literal_pathspec(path)])
        result = self._git.run(args, cwd=cwd)
        if result.returncode != 0:
            return []
        items: list[GitDiffStat] = []
        for line in result.stdout.splitlines():
            additions, separator, rest = line.partition("\t")
            deletions, separator2, changed_path = rest.partition("\t")
            if not separator or not separator2:
                continue
            binary = additions == "-" or deletions == "-"
            items.append(
                GitDiffStat(
                    path=changed_path,
                    additions=0 if binary else int(additions or 0),
                    deletions=0 if binary else int(deletions or 0),
                    binary=binary,
                )
            )
        return items

    def untracked_paths(self, cwd: Path, path_filter: str = "") -> list[str]:
        args = ["ls-files", "--others", "--exclude-standard"]
        if path_filter:
            args.extend(["--", literal_pathspec(path_filter)])
        result = self._git.run(args, cwd=cwd)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()][:200]

    def untracked_diff(self, cwd: Path, path_filter: str) -> tuple[str, bool]:
        output: list[str] = []
        truncated = False
        for path in self.untracked_paths(cwd, path_filter)[:50]:
            try:
                (cwd / path).resolve().relative_to(cwd.resolve())
            except ValueError:
                continue
            result = self._git.run(
                ["diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--", "/dev/null", path],
                cwd=cwd,
            )
            if result.returncode not in {0, 1}:
                continue
            output.append(result.stdout)
            truncated = truncated or result.truncated
            if sum(len(item) for item in output) > _MAX_UNTRACKED_DIFF_CHARS:
                truncated = True
                break
        text = "".join(output)
        return text[:_MAX_UNTRACKED_DIFF_CHARS], truncated or len(text) > _MAX_UNTRACKED_DIFF_CHARS

    def untracked_stats(self, cwd: Path, path_filter: str) -> list[GitDiffStat]:
        return [
            GitDiffStat(path=path, additions=self.line_count(cwd / path, root=cwd))
            for path in self.untracked_paths(cwd, path_filter)
        ]

    @staticmethod
    def line_count(path: Path, *, root: Path) -> int:
        try:
            resolved = path.resolve()
            resolved.relative_to(root.resolve())
            with resolved.open("rb") as handle:
                raw = handle.read(1_000_001)
            if len(raw) > 1_000_000:
                return 0
            if b"\x00" in raw:
                return 0
            return len(raw.decode("utf-8", errors="replace").splitlines())
        except (OSError, ValueError):
            return 0

    def ahead_behind(self, cwd: Path, upstream: str) -> tuple[int, int]:
        if not upstream:
            return 0, 0
        result = self._git.run(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd=cwd)
        if result.returncode != 0:
            return 0, 0
        left, _, right = result.stdout.strip().partition("\t")
        try:
            return int(left or 0), int(right or 0)
        except ValueError:
            return 0, 0

    def operation_state(self, cwd: Path) -> str:
        git_dir_result = self._git.run(["rev-parse", "--git-dir"], cwd=cwd)
        if git_dir_result.returncode != 0:
            return "unknown"
        git_dir = Path(git_dir_result.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (cwd / git_dir).resolve()
        checks = (
            ("MERGE_HEAD", "merge"),
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("REVERT_HEAD", "revert"),
            ("BISECT_LOG", "bisect"),
        )
        return next((state for marker, state in checks if (git_dir / marker).exists()), "idle")

    def head_sha(self, cwd: Path) -> str:
        head = self._git.run(["rev-parse", "--verify", "HEAD"], cwd=cwd)
        return head.stdout.strip() if head.returncode == 0 else ""

    def upstream(self, cwd: Path) -> str:
        result = self._git.run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=cwd)
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def commit_from_line(line: str, separator: str) -> GitCommitSummary | None:
        parts = line.split(separator)
        if len(parts) < 8:
            return None
        sha, short_sha, subject, author_name, author_email, authored_at, parents, refs = parts[:8]
        return GitCommitSummary(
            sha=sha,
            short_sha=short_sha,
            subject=subject,
            author_name=author_name,
            author_email=author_email,
            authored_at=authored_at,
            parents=[item for item in parents.split() if item],
            refs=[item.strip() for item in refs.split(",") if item.strip()],
        )

    def reflog_activity(self, workspace: WorkspaceRef, limit: int) -> list[GitActivityEvent]:
        separator = "\x1f"
        result = self._git.run(
            [
                "reflog",
                "--all",
                f"--max-count={limit}",
                "--date=iso-strict",
                f"--format=%H{separator}%gd{separator}%gs{separator}%gn",
            ],
            cwd=workspace.root,
        )
        if result.returncode != 0:
            return []
        events: list[GitActivityEvent] = []
        for line in result.stdout.splitlines():
            parts = line.split(separator)
            if len(parts) < 4:
                continue
            sha, selector, summary, actor = parts[:4]
            timestamp_match = re.search(r"@\{(.+)\}$", selector)
            timestamp = timestamp_match.group(1) if timestamp_match else ""
            operation = summary.partition(":")[0].strip() or "reflog"
            events.append(
                GitActivityEvent(
                    id=f"reflog-{sha[:12]}",
                    timestamp=timestamp,
                    actor=actor or "git",
                    operation=operation,
                    action=operation,
                    outcome="observed",
                    source="git_reflog",
                    workspace_id=workspace.workspace_id,
                    summary=summary,
                )
            )
        return events
