import sys

import scripts.release_gate as release_gate


def _check(name, ok=True, detail="unit"):
    return release_gate.CheckResult(name, ok, detail)


def test_release_gate_report_marks_negative_checks_as_failed():
    report = release_gate.build_report([_check("required-files"), _check("todo-status", ok=False, detail="drift")])

    assert report["ok"] is False
    assert report["checks"][1] == {"name": "todo-status", "ok": False, "detail": "drift"}


def test_release_gate_non_strict_skips_final_strict_checks(monkeypatch, capsys):
    calls = []

    def make_check(name, ok=True):
        def _check_fn():
            calls.append(name)
            return _check(name, ok=ok)

        return _check_fn

    for name in (
        "required_files",
        "python_dependency_sources",
        "python_locks",
        "frontend_manifest",
        "actions_pinning",
        "image_pinning",
        "tool_pinning",
        "ci_release_paths",
        "apt_snapshots",
        "todo_status",
    ):
        monkeypatch.setattr(release_gate, f"check_{name}", make_check(name.replace("_", "-")))
    monkeypatch.setattr(sys, "argv", ["release_gate.py"])

    assert release_gate.main() == 0

    output = capsys.readouterr().out
    assert "actions-pinning" not in output
    assert "apt-snapshots" not in output
    assert "actions-pinning" in calls
    assert "apt-snapshots" in calls


def test_release_gate_strict_keeps_actions_and_apt_snapshot_failures(monkeypatch, capsys):
    def make_check(name, ok=True):
        return lambda: _check(name, ok=ok, detail=f"{name} detail")

    replacements = {
        "required_files": make_check("required-files"),
        "python_dependency_sources": make_check("python-dependency-sources"),
        "python_locks": make_check("python-locks"),
        "frontend_manifest": make_check("frontend-manifest"),
        "actions_pinning": make_check("actions-pinning", ok=False),
        "image_pinning": make_check("image-pinning"),
        "tool_pinning": make_check("tool-pinning"),
        "ci_release_paths": make_check("ci-release-paths"),
        "apt_snapshots": make_check("apt-snapshots", ok=False),
        "todo_status": make_check("todo-status"),
    }
    for attr, replacement in replacements.items():
        monkeypatch.setattr(release_gate, f"check_{attr}", replacement)
    monkeypatch.setattr(sys, "argv", ["release_gate.py", "--strict"])

    assert release_gate.main() == 1

    output = capsys.readouterr().out
    assert "[FAIL] actions-pinning" in output
    assert "[FAIL] apt-snapshots" in output
