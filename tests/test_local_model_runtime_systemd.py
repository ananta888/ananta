from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _unit(name: str) -> str:
    return (ROOT / "deploy/systemd" / name).read_text(encoding="utf-8")


def test_runtime_supervision_is_bounded_and_uses_one_control_group():
    unit = _unit("ananta-local-model-runtime.service")

    assert "Restart=on-failure" in unit
    assert "StartLimitIntervalSec=900" in unit
    assert "StartLimitBurst=3" in unit
    assert "KillMode=control-group" in unit
    assert "Type=notify" in unit
    assert "NotifyAccess=all" in unit
    assert "NoNewPrivileges=true" in unit
    assert "EnvironmentFile=%h/ananta/data/local-model-runtime/runtime.env" in unit
    assert "control.env" not in unit
    assert "ExecStop=" not in unit


def test_colibri_runtime_build_is_revision_pinned_and_source_restoring():
    script = (ROOT / "scripts/build-colibri-qwen36-runtime.sh").read_text(encoding="utf-8")
    patch = (ROOT / "scripts/patches/colibri-qwen36-request-hit-rate.patch").read_text(encoding="utf-8")

    assert 'EXPECTED_REVISION="33e67a9c004b6e608d1f19dfbdcc20793377f94f"' in script
    assert "trap restore_source EXIT" in script
    assert 'git -C "$COLIBRI_ROOT" apply --reverse' in script
    assert "qt_counters" in patch
    assert "request_hits" in patch


def test_control_bridge_restart_is_bounded_too():
    unit = _unit("ananta-local-model-control.service")

    assert "StartLimitIntervalSec=900" in unit
    assert "StartLimitBurst=3" in unit
    assert "NoNewPrivileges=true" in unit
    assert "EnvironmentFile=%h/ananta/data/local-model-runtime/control.env" in unit
    assert "runtime.env" not in unit
