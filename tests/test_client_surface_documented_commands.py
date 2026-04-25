from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_doc(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_tui_documented_start_command_is_validated_by_existing_runtime_entrypoint() -> None:
    guide = _read_doc("docs/tui-user-operator-guide.md")
    assert "python -m client_surfaces.tui_runtime.ananta_tui --fixture" in guide
    assert (ROOT / "client_surfaces/tui_runtime/ananta_tui/__main__.py").exists()
    assert (ROOT / "scripts/smoke_tui_runtime.py").exists()


def test_neovim_and_vim_docs_provide_smoke_or_explicit_deferred_status() -> None:
    nvim_guide = _read_doc("docs/nvim-plugin-user-guide.md")
    status_guide = _read_doc("docs/plugin-vs-tui-usage-guide.md")

    assert "python3 scripts/smoke_nvim_runtime.py" in nvim_guide
    assert (ROOT / "scripts/smoke_nvim_runtime.py").exists()
    assert "Vim compatibility: deferred" in status_guide


def test_eclipse_docs_include_build_and_smoke_commands_with_existing_scripts() -> None:
    eclipse_bootstrap = _read_doc("docs/eclipse-plugin-runtime-bootstrap.md")

    assert "python3 scripts/build_eclipse_runtime_plugin.py --mode build" in eclipse_bootstrap
    assert "python3 scripts/smoke_eclipse_runtime_bootstrap.py" in eclipse_bootstrap
    assert "python3 scripts/smoke_eclipse_runtime_headless.py" in eclipse_bootstrap

    assert (ROOT / "scripts/build_eclipse_runtime_plugin.py").exists()
    assert (ROOT / "scripts/smoke_eclipse_runtime_bootstrap.py").exists()
    assert (ROOT / "scripts/smoke_eclipse_runtime_headless.py").exists()


def test_release_docs_include_consolidated_client_surface_test_gate_command() -> None:
    release_guide = _read_doc("docs/release-golden-path.md")
    assert "python3 scripts/run_client_surface_test_gate.py --out ci-artifacts/client-surface-test-gate.json" in release_guide
    assert (ROOT / "scripts/run_client_surface_test_gate.py").exists()
