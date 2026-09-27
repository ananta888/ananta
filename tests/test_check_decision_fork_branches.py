"""The fork drift check: identical decision code passes, a one-sided change is named."""

import subprocess

import pytest

from scripts import check_decision_fork_branches as check

pytestmark = pytest.mark.timeout(60)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(repo), "PATH": "/usr/bin:/bin"})


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "vision-decision")
    (tmp_path / "tools/parallel-decision").mkdir(parents=True)
    (tmp_path / "tools/parallel-decision/decision-engine.cpp").write_text("engine\n")
    (tmp_path / "base.cpp").write_text("mainline\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "branch", "bonsai-decision")
    return tmp_path


def test_base_specific_changes_are_not_drift(repo, capsys):
    git(repo, "checkout", "-q", "bonsai-decision")
    (repo / "base.cpp").write_text("prism\n")
    git(repo, "commit", "-qam", "prism base")
    assert check.main(["--repo", str(repo), "--remote", ""]) == 0
    assert "identical" in capsys.readouterr().out


def test_a_decision_change_on_one_branch_is_reported(repo, capsys):
    (repo / "tools/parallel-decision/decision-engine.cpp").write_text("engine v2\n")
    git(repo, "commit", "-qam", "only on vision")
    assert check.main(["--repo", str(repo), "--remote", ""]) == 1
    assert "tools/parallel-decision/decision-engine.cpp" in capsys.readouterr().out
