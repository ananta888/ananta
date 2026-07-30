from __future__ import annotations

import subprocess
from pathlib import Path

from agent.common.audit import log_audit
from agent.services.git_audit_service import git_workspace_fingerprint
from agent.services.git_ops_service import GitOpsService
from agent.services.ops_policy_service import OpsPolicyDecision, OpsPolicyService
from agent.services.ops_registry_service import OpsRegistryService, WorkspaceRef


class AllowPolicy(OpsPolicyService):
    def evaluate(self, tool_name: str, action: str, *, target_id: str = "") -> OpsPolicyDecision:
        del tool_name, action, target_id
        return OpsPolicyDecision("allow", "test_allow")


class GrantedPolicy(OpsPolicyService):
    def __init__(self) -> None:
        self.authorized: list[dict] = []
        self.consumed: list[str] = []

    def authorize(self, tool_name, action, *, target_id="", arguments=None, approval_id=None):
        self.authorized.append(
            {
                "tool_name": tool_name,
                "action": action,
                "target_id": target_id,
                "arguments": arguments,
                "approval_id": approval_id,
            }
        )
        return OpsPolicyDecision("allow", "approval_granted")

    def consume_approval(self, approval_id):
        if approval_id:
            self.consumed.append(str(approval_id))


class DeniedApprovalPolicy(OpsPolicyService):
    def authorize(self, tool_name, action, *, target_id="", arguments=None, approval_id=None):
        del tool_name, action, target_id, arguments, approval_id
        return OpsPolicyDecision("policy_denied", "approval_digest_mismatch")


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(  # noqa: S603 - controlled test fixture command.
        ["git", *args],  # noqa: S607 - test environment provides Git.
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.name", "Ops Test"], repo)
    _git(["config", "user.email", "ops@example.invalid"], repo)
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo)
    _git(["commit", "-m", "chore(test): initialize repository"], repo)
    return repo


def _service(repo: Path, *, policy=None, workspace_id: str = "w1") -> GitOpsService:
    registry = OpsRegistryService(workspaces=[WorkspaceRef(workspace_id, repo)])
    return GitOpsService(registry=registry, policy=policy or AllowPolicy())


def test_combined_diff_separates_staged_unstaged_and_untracked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("base\nstaged\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo)
    (repo / "tracked.txt").write_text("base\nstaged\nworking\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")
    service = _service(repo)

    status = service.status("w1")
    diff = service.diff("w1", scope="combined")

    assert status.error is None
    assert status.staged_count == 1
    assert status.unstaged_count == 1
    assert status.untracked_count == 1
    assert "staged" in diff.staged_diff
    assert "working" in diff.unstaged_diff
    assert "untracked.txt" in diff.untracked_diff
    assert "untracked.txt" in diff.diff
    assert diff.files_changed >= 2


def test_literal_pathspec_prevents_magic_path_from_staging_other_files(tmp_path):
    repo = _repo(tmp_path)
    magic_name = ":(glob)*.txt"
    (repo / magic_name).write_text("magic\n", encoding="utf-8")
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    service = _service(repo)

    result = service.stage("w1", [magic_name])
    staged_names = _git(["diff", "--cached", "--name-only", "-z"], repo).split("\0")

    assert result.ok is True
    assert magic_name in staged_names
    assert "other.txt" not in staged_names


def test_discard_preserves_index_and_never_deletes_untracked_file(tmp_path):
    repo = _repo(tmp_path)
    tracked = repo / "tracked.txt"
    tracked.write_text("base\nstaged\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo)
    tracked.write_text("base\nstaged\nworking\n", encoding="utf-8")
    untracked = repo / "untracked.txt"
    untracked.write_text("keep\n", encoding="utf-8")
    service = _service(repo)

    discarded = service.discard("w1", ["tracked.txt"])
    denied = service.discard("w1", ["untracked.txt"])
    status = service.status("w1")

    assert discarded.ok is True
    assert tracked.read_text(encoding="utf-8") == "base\nstaged\n"
    tracked_state = next(item for item in status.changed_files if item.path == "tracked.txt")
    assert tracked_state.staged is True
    assert tracked_state.unstaged is False
    assert denied.error.code == "git_untracked_discard_denied"
    assert untracked.exists()


def test_mutation_passes_exact_arguments_and_consumes_one_shot_approval(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    policy = GrantedPolicy()
    service = _service(repo, policy=policy)

    result = service.stage("w1", ["new.txt"], approval_id="grant-1")

    assert result.ok is True
    assert policy.authorized == [
        {
            "tool_name": "git.stage",
            "action": "stage",
            "target_id": "w1",
            "arguments": {"workspace_id": "w1", "paths": ["new.txt"]},
            "approval_id": "grant-1",
        }
    ]
    assert policy.consumed == ["grant-1"]


def test_denied_approval_id_is_not_returned_as_retryable_request(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    service = _service(repo, policy=DeniedApprovalPolicy())

    result = service.stage("w1", ["new.txt"], approval_id="wrong-grant")

    assert result.ok is False
    assert result.decision == "policy_denied"
    assert result.error.code == "policy_denied"
    assert result.error.message == "approval_digest_mismatch"
    assert result.approval_id is None


def test_history_can_be_filtered_by_workspace_relative_path(tmp_path):
    repo = _repo(tmp_path)
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _git(["add", "second.txt"], repo)
    _git(["commit", "-m", "feat(test): add second file"], repo)
    service = _service(repo)

    tracked_history = service.history("w1", path="tracked.txt")
    second_history = service.history("w1", path="second.txt")

    assert [item.subject for item in tracked_history.items] == ["chore(test): initialize repository"]
    assert [item.subject for item in second_history.items] == ["feat(test): add second file"]
    assert service.history("w1", path="../outside").error.code == "path_not_allowed"


def test_activity_filters_internal_git_events_by_workspace_fingerprint(app, tmp_path):
    first = _repo(tmp_path, "first")
    second = _repo(tmp_path, "second")
    first_fingerprint = git_workspace_fingerprint(first)
    second_fingerprint = git_workspace_fingerprint(second)
    with app.app_context():
        log_audit(
            "workspace_git_commit_push",
            {"workspace_fingerprint": first_fingerprint, "outcome": "committed", "summary": "first workspace"},
        )
        log_audit(
            "workspace_git_commit_push",
            {"workspace_fingerprint": second_fingerprint, "outcome": "committed", "summary": "second workspace"},
        )
        activity = _service(first).activity("w1")

    summaries = [item.summary for item in activity.items]
    assert "first workspace" in summaries
    assert "second workspace" not in summaries
    internal = next(item for item in activity.items if item.summary == "first workspace")
    assert internal.operation == "commit_push"
    assert internal.outcome == "committed"


def test_network_actions_reject_untrusted_local_filesystem_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(  # noqa: S603 - controlled test fixture command.
        ["git", "init", "--bare", str(remote)],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    repo = _repo(tmp_path)
    _git(["remote", "add", "origin", str(remote)], repo)
    branch = _git(["branch", "--show-current"], repo).strip()
    _git(["push", "--set-upstream", "origin", branch], repo)
    service = _service(repo)

    (repo / "tracked.txt").write_text("base\nlocal\n", encoding="utf-8")
    _git(["add", "tracked.txt"], repo)
    _git(["commit", "-m", "feat(test): add local change"], repo)
    pushed = service.push("w1")

    fetched = service.fetch("w1")
    pulled = service.pull("w1")

    assert pushed.ok is False
    assert fetched.ok is False
    assert pulled.ok is False
    assert pushed.error.code == "git_remote_url_invalid"
    assert fetched.error.code == "git_remote_url_invalid"
    assert pulled.error.code == "git_remote_url_invalid"


def test_fetch_rejects_untrusted_local_remote_even_when_registered(tmp_path):
    origin = tmp_path / "origin.git"
    backup = tmp_path / "backup.git"
    for remote in (origin, backup):
        subprocess.run(  # noqa: S603 - controlled test fixture command.
            ["git", "init", "--bare", str(remote)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    repo = _repo(tmp_path)
    branch = _git(["branch", "--show-current"], repo).strip()
    _git(["remote", "add", "origin", str(origin)], repo)
    _git(["remote", "add", "backup", str(backup)], repo)
    _git(["push", "--set-upstream", "origin", branch], repo)
    service = _service(repo)

    alternate = service.fetch("w1", remote="backup")
    _git(["checkout", "--detach"], repo)
    detached = service.fetch("w1", remote="backup")

    assert alternate.ok is False
    assert detached.ok is False
    assert alternate.error.code == "git_remote_url_invalid"
    assert detached.error.code == "git_remote_url_invalid"
    assert service.fetch("w1", remote="missing").error.code == "git_remote_not_allowed"
