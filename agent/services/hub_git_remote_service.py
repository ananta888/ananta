"""HubGitRemoteService — manages bare git repos that act as the hub-side remote for goal workspaces."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from agent.services.workspace_git_service import (
    MANAGED_BARE_MARKER_NAME,
    MANAGED_BARE_MARKER_VALUE,
)

_REPOS_ROOT = Path("/project-workspaces/git-repos")


class HubGitRemoteError(RuntimeError):
    pass


class HubGitRemoteService:
    """Manages bare git repos under /project-workspaces/git-repos/<goal_id[:12]>.git."""

    def __init__(self, repos_root: Path = _REPOS_ROOT) -> None:
        self._repos_root = Path(repos_root)

    def _repo_path(self, goal_id: str) -> Path:
        safe = str(goal_id or "").strip()[:12].replace("/", "-").replace("..", "")
        if not safe:
            raise HubGitRemoteError("goal_id required")
        return self._repos_root / f"{safe}.git"

    def create_goal_repo(self, goal_id: str) -> Path:
        """Ensure a bare git repo exists for goal_id. Returns repo path. Idempotent."""
        repo = self._repo_path(goal_id)
        if repo.exists():
            self._mark_managed(repo)
            return repo
        repo.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "init", "--bare", str(repo)],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            raise HubGitRemoteError(f"git init --bare failed for goal {goal_id}: {res.stderr}")
        self._mark_managed(repo)
        logging.info("Hub bare repo created: %s", repo)
        return repo

    @staticmethod
    def _mark_managed(repo: Path) -> None:
        """Declare this bare repo Ananta-managed, which it is by construction.

        get_remote_url hands workers a ``file://`` URL for this repo, and a
        local remote is only accepted once it proves it is Ananta-managed.
        Without the marker the Hub was handing out remotes its own workers then
        refused, failing every clone with ``git_remote_url_invalid``.

        Written on the existing-repo path too, so repos created before this are
        healed rather than staying permanently unusable.
        """

        marker = repo / MANAGED_BARE_MARKER_NAME
        try:
            if marker.is_symlink():
                return
            if marker.is_file() and marker.read_text(encoding="ascii") == MANAGED_BARE_MARKER_VALUE:
                return
            marker.write_text(MANAGED_BARE_MARKER_VALUE, encoding="ascii")
        except (OSError, UnicodeError):
            # A repo that cannot be marked stays unusable as a local remote,
            # which is the safe outcome; creating the repo still succeeds.
            logging.warning("Could not mark bare repo as Ananta-managed: %s", repo)

    def get_remote_url(self, goal_id: str) -> str:
        """Return the file:// URL workers should clone from."""
        repo = self._repo_path(goal_id)
        return f"file://{repo}"

    def list_branches(self, goal_id: str) -> list[str]:
        """List branches in the bare repo. Returns [] if repo does not exist."""
        repo = self._repo_path(goal_id)
        if not repo.exists():
            return []
        res = subprocess.run(
            ["git", "--git-dir", str(repo), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            return []
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]

    def merge_worker_branch(
        self,
        goal_id: str,
        *,
        source_branch: str,
        target_branch: str = "main",
        strategy: str = "ours",
    ) -> bool:
        """Merge source_branch into target_branch in a temporary clone. Returns True on success."""
        repo = self._repo_path(goal_id)
        if not repo.exists():
            raise HubGitRemoteError(f"Repo for goal {goal_id} does not exist")
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="ananta-merge-"))
        try:
            res = subprocess.run(
                ["git", "clone", f"file://{repo}", str(tmp), "--no-local"],
                capture_output=True, text=True, timeout=60,
            )
            if res.returncode != 0:
                raise HubGitRemoteError(f"Clone for merge failed: {res.stderr}")

            def _git(*args):
                return subprocess.run(
                    ["git"] + list(args), cwd=str(tmp),
                    capture_output=True, text=True, timeout=30,
                )

            # Checkout or create target
            r = _git("checkout", target_branch)
            if r.returncode != 0:
                r = _git("checkout", "-b", target_branch)
                if r.returncode != 0:
                    raise HubGitRemoteError(f"Cannot checkout {target_branch}: {r.stderr}")

            # Fetch source
            _git("fetch", "origin", source_branch)
            merge_flags = ["-X", "ours"] if strategy == "ours" else []
            r = _git("merge", "--no-edit", *merge_flags, f"origin/{source_branch}")
            if r.returncode != 0:
                logging.warning("Merge %s -> %s failed: %s", source_branch, target_branch, r.stderr)
                return False

            r = _git("push", "origin", f"HEAD:{target_branch}")
            if r.returncode != 0:
                logging.warning("Push after merge failed: %s", r.stderr)
                return False
            return True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def repo_exists(self, goal_id: str) -> bool:
        return self._repo_path(goal_id).exists()


_instance: Optional[HubGitRemoteService] = None


def get_hub_git_remote_service() -> HubGitRemoteService:
    global _instance
    if _instance is None:
        _instance = HubGitRemoteService()
    return _instance
