from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_bootstrap_docs_and_scripts_exist() -> None:
    assert (ROOT / "scripts" / "install-ananta.ps1").exists()
    assert (ROOT / "scripts" / "install-ananta.sh").exists()
    assert (ROOT / "docs" / "setup" / "bootstrap-install.md").exists()
    assert (ROOT / "docs" / "setup" / "ananta_update.md").exists()


def test_bootstrap_doc_includes_safe_and_safer_install_variants() -> None:
    bootstrap_doc = _read("docs/setup/bootstrap-install.md")
    assert "scripts/install-ananta.ps1" in bootstrap_doc
    assert "scripts/install-ananta.sh" in bootstrap_doc
    assert "raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.ps1" in bootstrap_doc
    assert "raw.githubusercontent.com/ananta888/ananta/main/scripts/install-ananta.sh" in bootstrap_doc
    assert "Get-Content .\\install-ananta.ps1" in bootstrap_doc
    assert "sed -n '1,200p' install-ananta.sh" in bootstrap_doc
    assert "local CLI usage does **not** require Docker".lower() in bootstrap_doc.lower()


def test_bootstrap_docs_include_next_steps_and_update_guidance() -> None:
    bootstrap_doc = _read("docs/setup/bootstrap-install.md")
    update_doc = _read("docs/setup/ananta_update.md")
    quickstart_doc = _read("docs/setup/quickstart.md")
    readme = _read("README.md")
    installer_sh = _read("scripts/install-ananta.sh")
    installer_ps1 = _read("scripts/install-ananta.ps1")

    assert "ananta init" in bootstrap_doc
    assert "ananta doctor" in bootstrap_doc
    assert "ananta status" in bootstrap_doc
    assert "ananta update --help" in bootstrap_doc
    assert "--endpoint-url" in bootstrap_doc
    assert "--base-url" not in bootstrap_doc
    assert "--endpoint-url" in installer_sh
    assert "--base-url" not in installer_sh
    assert "--endpoint-url" in installer_ps1
    assert "--base-url" not in installer_ps1
    assert "rollback" in update_doc.lower()
    assert "docs/setup/bootstrap-install.md" in quickstart_doc
    assert "docs/setup/bootstrap-install.md" in readme
