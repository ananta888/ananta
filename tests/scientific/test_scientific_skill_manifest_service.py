from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from agent.services.scientific_skill_manifest_service import (
    LoadedScientificSkillPackage,
    LocalScientificSkillPackageLoader,
    ScientificSkillManifestError,
    ScientificSkillManifestImporter,
)


def _package(root: Path, *, body: str = "See [guide](references/guide.md).") -> Path:
    (root / "skills" / "demo" / "references").mkdir(parents=True)
    (root / "skills" / "demo" / "scripts").mkdir()
    (root / "plugin.json").write_text(
        json.dumps({"name": "scientific-demo", "license": "MIT"}),
        encoding="utf-8",
    )
    (root / "skills" / "demo" / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: Bounded research helper\n---\n{body}\n",
        encoding="utf-8",
    )
    (root / "skills" / "demo" / "references" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "skills" / "demo" / "scripts" / "never_run.py").write_text(
        "raise RuntimeError('must never execute')\n",
        encoding="utf-8",
    )
    return root


def _inspect(path: Path):
    return ScientificSkillManifestImporter().inspect(
        package_path=path,
        upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
        upstream_pin="0123456789abcdef0123456789abcdef01234567",
    )


def test_directory_inventory_is_deterministic_declarative_and_never_executes_scripts(tmp_path: Path) -> None:
    package = _package(tmp_path / "package", body="See [guide](references/guide.md) and [source](https://example.test/paper).")
    first = _inspect(package)
    second = _inspect(package)
    assert first == second
    assert first.package_name == "scientific-demo"
    assert first.license == "MIT"
    assert len(first.skills) == 1
    skill = first.skills[0]
    assert (skill.name, skill.upstream_path, skill.upstream_pin) == (
        "demo",
        "skills/demo/SKILL.md",
        "0123456789abcdef0123456789abcdef01234567",
    )
    assert skill.source_references == ("https://example.test/paper",)
    assert {item.kind for item in skill.declared_files} == {"skill", "reference", "script"}
    script = next(item for item in skill.declared_files if item.kind == "script")
    assert script.language == "python"


def test_zip_package_is_inspected_without_extracting_or_writing(tmp_path: Path) -> None:
    source = _package(tmp_path / "source")
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, f"scientific-agent-skills-pin/{path.relative_to(source).as_posix()}")
    before = set(tmp_path.iterdir())
    manifest = _inspect(archive_path)
    assert manifest.skills[0].name == "demo"
    assert set(tmp_path.iterdir()) == before


def test_mit_license_file_is_used_when_plugin_has_no_license_field(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    (package / "plugin.json").write_text(json.dumps({"name": "scientific-demo"}), encoding="utf-8")
    (package / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...", encoding="utf-8")
    assert _inspect(package).license == "MIT"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("See [missing](references/missing.md).", "reference_unresolved"),
        ("See [absolute](/etc/passwd).", "path_traversal_denied"),
        ("See [escape](../../outside.md).", "path_traversal_denied"),
        ("See [file](file:///etc/passwd).", "reference_scheme_denied"),
    ],
)
def test_unresolved_or_unsafe_references_fail_closed(tmp_path: Path, body: str, reason: str) -> None:
    package = _package(tmp_path / "package", body=body)
    with pytest.raises(ScientificSkillManifestError, match=reason):
        _inspect(package)


def test_invalid_frontmatter_and_referenced_binary_fail_closed(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    skill = package / "skills" / "demo" / "SKILL.md"
    skill.write_text("name: missing-fences\n", encoding="utf-8")
    with pytest.raises(ScientificSkillManifestError, match="frontmatter_invalid"):
        _inspect(package)

    package = _package(tmp_path / "binary", body="See [binary](references/data.bin).")
    (package / "skills" / "demo" / "references" / "data.bin").write_bytes(b"safe-prefix\x00binary")
    with pytest.raises(ScientificSkillManifestError, match="binary_file_denied"):
        _inspect(package)


def test_symlinks_and_archive_traversal_are_rejected_before_parsing(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    os.symlink(tmp_path / "outside", package / "skills" / "demo" / "escape")
    with pytest.raises(ScientificSkillManifestError, match="symlink_denied"):
        _inspect(package)

    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../plugin.json", "{}")
    with pytest.raises(ScientificSkillManifestError, match="path_traversal_denied"):
        _inspect(archive_path)


def test_oversized_declared_file_and_invalid_pin_fail_closed(tmp_path: Path) -> None:
    class _OversizedLoader:
        def load(self, _package_path: Path) -> LoadedScientificSkillPackage:
            return LoadedScientificSkillPackage(
                {
                    "plugin.json": b'{"name":"demo","license":"MIT"}',
                    "skills/demo/SKILL.md": (
                        b"---\nname: demo\ndescription: demo\n---\n"
                        + b"x" * (LocalScientificSkillPackageLoader.MAX_FILE_BYTES + 1)
                    ),
                }
            )

    with pytest.raises(ScientificSkillManifestError, match="file_size_invalid"):
        ScientificSkillManifestImporter(_OversizedLoader()).inspect(
            package_path=tmp_path,
            upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
            upstream_pin="v1.2.3",
        )
    with pytest.raises(ScientificSkillManifestError, match="pin_invalid"):
        ScientificSkillManifestImporter().inspect(
            package_path=tmp_path,
            upstream_repository="https://github.com/K-Dense-AI/scientific-agent-skills",
            upstream_pin="main",
        )
