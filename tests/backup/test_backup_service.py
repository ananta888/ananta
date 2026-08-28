from __future__ import annotations

import json
import tarfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

import pytest

from scripts.ananta_backup.backup_service import BackupRequest, BackupService
from scripts.ananta_backup.compose import ComposeLayout
from scripts.ananta_backup.errors import BackupError
from scripts.ananta_backup.filesystem import rename_directory_no_replace


class FakeArchive:
    def __init__(self) -> None:
        self.verified = False
        self.encrypted_roots: list[str | None] = []
        self.encrypted_environment: str | None = None

    def verify_public_recipient(self, recipient: object) -> None:
        self.verified = True

    def encrypt_trees(
        self, plans: Sequence[object], recipient: object, destination: Path
    ) -> None:
        self.encrypted_roots = [
            str(getattr(plan, "archive_root"))
            if getattr(plan, "archive_root") is not None
            else None
            for plan in plans
        ]
        payload_plan = next(
            plan for plan in plans if getattr(plan, "archive_root") is None
        )
        self.encrypted_environment = (
            getattr(payload_plan, "source") / "configuration" / ".env"
        ).read_text(encoding="utf-8")
        destination.write_bytes(b"encrypted-only")


class AllowTestWindowsTarget:
    def validate(self, target: Path) -> Path:
        return target

    def publish_no_replace(
        self,
        source: Path,
        destination: Path,
    ) -> None:
        rename_directory_no_replace(source, destination)


class FakeCompose:
    def __init__(self, layout: ComposeLayout) -> None:
        self.layout = layout
        self.preflight_count = 0
        self.pause_count = 0
        self.quiescence_count = 0
        self.dump_count = 0
        self.sqlite_checks: list[str] = []

    def load_layout(self) -> ComposeLayout:
        return self.layout

    def preflight(self, layout: ComposeLayout) -> None:
        assert layout is self.layout
        self.preflight_count += 1

    @contextmanager
    def paused_agents(self) -> Iterator[None]:
        self.pause_count += 1
        yield

    def assert_quiescent(self, layout: ComposeLayout) -> None:
        assert layout is self.layout
        self.quiescence_count += 1

    def dump_postgres(self, layout: ComposeLayout, destination: Path) -> Path:
        self.dump_count += 1
        destination.write_bytes(b"PGDMP-test")
        catalog = destination.with_suffix(".catalog.txt")
        catalog.write_text("TABLE public.task", encoding="utf-8")
        return catalog

    def capture_reproducibility(
        self, layout: ComposeLayout, destination: Path
    ) -> dict[str, str]:
        destination.mkdir()
        files = {
            ".env": "POSTGRES_PASSWORD=encrypted-only\n",
            "compose.rendered.json": "{}\n",
            "model_profiles.yaml": "profiles: {}\n",
            "model_routing.json": "{}\n",
            "git-state.json": '{"commit": "test", "dirty": false}\n',
        }
        for name, body in files.items():
            (destination / name).write_text(body, encoding="utf-8")
        return {
            "environment": "configuration/.env",
            "rendered_compose": "configuration/compose.rendered.json",
            "model_profiles": "configuration/model_profiles.yaml",
            "model_routing": "configuration/model_routing.json",
            "git_state": "configuration/git-state.json",
        }

    def verify_worker_sqlite_archive(
        self,
        archive_path: Path,
        logical_name: str,
    ) -> None:
        assert archive_path.name == f"{logical_name}.tar"
        self.sqlite_checks.append(logical_name)

    def export_volume(
        self,
        layout: ComposeLayout,
        logical_name: str,
        destination_directory: Path,
    ) -> Path:
        destination = destination_directory / f"{logical_name}.tar"
        with tarfile.open(destination, "w") as archive:
            entry = tarfile.TarInfo("marker")
            body = logical_name.encode()
            entry.size = len(body)
            import io

            archive.addfile(entry, io.BytesIO(body))
        return destination


def _layout(tmp_path: Path) -> ComposeLayout:
    workflow = tmp_path / "workflow"
    workspaces = tmp_path / "workspaces"
    workflow.mkdir()
    workspaces.mkdir()
    (workflow / "signing-key").write_text("secret", encoding="utf-8")
    return ComposeLayout(
        project_name="compose-next",
        runtime_image="ananta:test",
        runtime_user="1000:1000",
        postgres_image="postgres:test",
        postgres_user="ananta",
        postgres_database="ananta",
        named_volumes={
            "hub-data": "compose-next_hub-data",
            "alpha-data": "compose-next_alpha-data",
            "beta-data": "compose-next_beta-data",
        },
        ollama_root=tmp_path / "ollama-not-needed",
        workflow_secret_root=workflow,
        workspace_root=workspaces,
        model_profiles_file=tmp_path / "profiles.yaml",
        model_routing_file=tmp_path / "routing.json",
    )


def _request(tmp_path: Path, *, dry_run: bool = False) -> BackupRequest:
    wsl = tmp_path / "wsl"
    windows = tmp_path / "windows"
    wsl.mkdir(exist_ok=True)
    windows.mkdir(exist_ok=True)
    recipient = tmp_path / "recipient.txt"
    recipient.write_text("A" * 40 + "\n", encoding="ascii")
    recipient.chmod(0o600)
    return BackupRequest(
        wsl_target=wsl,
        windows_target=windows,
        recipient_file=recipient,
        backup_name="ananta-backup-test",
        dry_run=dry_run,
    )


def test_backup_publishes_ciphertext_and_checksum_only_to_both_targets(
    tmp_path: Path,
) -> None:
    compose = FakeCompose(_layout(tmp_path))
    archive = FakeArchive()
    service = BackupService(
        compose,  # type: ignore[arg-type]
        archive,  # type: ignore[arg-type]
        AllowTestWindowsTarget(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 7, 26, tzinfo=timezone.utc),
    )

    result = service.run(_request(tmp_path))

    assert result.sha256 is not None
    assert compose.pause_count == 1
    assert compose.quiescence_count == 1
    assert compose.dump_count == 1
    assert compose.sqlite_checks == ["alpha-data", "beta-data"]
    for package in (result.wsl_package, result.windows_package):
        assert sorted(path.name for path in package.iterdir()) == [
            "ananta-backup-test.tar.zst.gpg",
            "checksum.json",
        ]
        assert (package / "ananta-backup-test.tar.zst.gpg").read_bytes() == (
            b"encrypted-only"
        )
        checksum = json.loads((package / "checksum.json").read_text(encoding="utf-8"))
        assert checksum["encrypted"] is True
        assert checksum["sha256"] == result.sha256
        assert "POSTGRES_PASSWORD" not in json.dumps(checksum)
    assert "bind/ollama/models" not in archive.encrypted_roots
    assert archive.encrypted_environment == "POSTGRES_PASSWORD=encrypted-only\n"


def test_backup_dry_run_performs_preflight_without_writes_or_pause(
    tmp_path: Path,
) -> None:
    compose = FakeCompose(_layout(tmp_path))
    archive = FakeArchive()
    service = BackupService(  # type: ignore[arg-type]
        compose, archive, AllowTestWindowsTarget()
    )
    request = _request(tmp_path, dry_run=True)

    result = service.run(request)

    assert result.dry_run is True
    assert compose.preflight_count == 1
    assert compose.pause_count == 0
    assert compose.quiescence_count == 0
    assert not result.wsl_package.exists()
    assert not result.windows_package.exists()


def test_backup_is_no_clobber_across_both_targets(tmp_path: Path) -> None:
    compose = FakeCompose(_layout(tmp_path))
    archive = FakeArchive()
    service = BackupService(  # type: ignore[arg-type]
        compose, archive, AllowTestWindowsTarget()
    )
    request = _request(tmp_path)
    (request.windows_target / "ananta-backup-test").mkdir()

    with pytest.raises(BackupError, match="already exists"):
        service.run(request)

    assert compose.preflight_count == 0


def test_ollama_option_includes_models_subtree_only(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.ollama_root / "models").mkdir(parents=True)
    (layout.ollama_root / "models" / "blob").write_text("model", encoding="utf-8")
    (layout.ollama_root / "id_ed25519").write_text("host identity", encoding="utf-8")
    compose = FakeCompose(layout)
    archive = FakeArchive()
    service = BackupService(  # type: ignore[arg-type]
        compose, archive, AllowTestWindowsTarget()
    )
    request = _request(tmp_path)
    request = BackupRequest(
        wsl_target=request.wsl_target,
        windows_target=request.windows_target,
        recipient_file=request.recipient_file,
        backup_name=request.backup_name,
        include_ollama_models=True,
    )

    service.run(request)

    assert "bind/ollama/models" in archive.encrypted_roots
    assert "bind/ollama" not in archive.encrypted_roots
