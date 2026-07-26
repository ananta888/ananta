from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

from scripts.ananta_backup.backup_service import BackupService
from scripts.ananta_backup.errors import BackupError
from scripts.ananta_backup.restore_service import RestoreRequest, RestoreService


class PreparedPayloadArchive:
    def __init__(self, payload: Path) -> None:
        self.payload = payload
        self.decrypt_count = 0
        self.metadata_paths: list[Path] = []
        self.decrypt_paths: list[Path] = []

    def read_member_bytes(
        self,
        encrypted: Path,
        member_names: set[str],
        *,
        max_member_bytes: int = 1024 * 1024,
    ) -> dict[str, bytes]:
        self.metadata_paths.append(encrypted)
        return {
            name: (self.payload / name).read_bytes()
            for name in member_names
        }

    def decrypt_to_directory(self, encrypted: Path, destination: Path) -> None:
        self.decrypt_count += 1
        self.decrypt_paths.append(encrypted)
        shutil.copytree(self.payload, destination, dirs_exist_ok=True)


class SuccessfulPostgresDrill:
    def __init__(self) -> None:
        self.dumps: list[Path] = []

    def verify(self, dump: Path) -> None:
        self.dumps.append(dump)


class SuccessfulBindVerifier:
    def __init__(self) -> None:
        self.bind_trees: dict[str, Path] = {}

    def verify(self, bind_trees: dict[str, Path]) -> bool:
        self.bind_trees = dict(bind_trees)
        return "ollama_models" in bind_trees


def _volume_archive(path: Path, *, sqlite_database: bool) -> None:
    content = path.parent / f"{path.stem}-content"
    content.mkdir()
    if sqlite_database:
        connection = sqlite3.connect(content / "ananta.db")
        connection.execute("CREATE TABLE task (id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
    else:
        (content / "hub.txt").write_text("hub-data", encoding="utf-8")
    with tarfile.open(path, "w") as archive:
        archive.add(content, arcname=".")
    shutil.rmtree(content)


def _payload(tmp_path: Path, backup_name: str) -> Path:
    payload = tmp_path / "payload"
    (payload / "postgres").mkdir(parents=True)
    (payload / "volumes").mkdir()
    (payload / "bind" / "workflow-secrets").mkdir(parents=True)
    (payload / "bind" / "project-workspaces").mkdir()
    (payload / "configuration").mkdir()
    (payload / "bind" / "workflow-secrets" / "key").write_text(
        "secret", encoding="utf-8"
    )
    dump = payload / "postgres" / "ananta.dump"
    catalog = payload / "postgres" / "ananta.catalog.txt"
    dump.write_bytes(b"PGDMP-test")
    catalog.write_text("TABLE public.task", encoding="utf-8")
    configuration = {
        "environment": "configuration/.env",
        "rendered_compose": "configuration/compose.rendered.json",
        "model_profiles": "configuration/model_profiles.yaml",
        "model_routing": "configuration/model_routing.json",
        "git_state": "configuration/git-state.json",
    }
    for relative in configuration.values():
        (payload / relative).write_text("{}\n", encoding="utf-8")
    (payload / configuration["rendered_compose"]).write_text(
        json.dumps(
            {
                "services": {
                    "hub": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(tmp_path / "live-workspaces"),
                                "target": "/project-workspaces",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _volume_archive(payload / "volumes" / "hub-data.tar", sqlite_database=False)
    _volume_archive(payload / "volumes" / "alpha-data.tar", sqlite_database=True)
    _volume_archive(payload / "volumes" / "beta-data.tar", sqlite_database=True)

    generated: dict[str, dict[str, object]] = {}
    for path in [
        dump,
        catalog,
        *(payload / "volumes").iterdir(),
        *(payload / "configuration").iterdir(),
    ]:
        body = path.read_bytes()
        generated[path.relative_to(payload).as_posix()] = {
            "sha256": hashlib.sha256(body).hexdigest(),
            "size_bytes": len(body),
        }
    (payload / "backup.json").write_text(
        json.dumps(
            {
                "schema": BackupService.PAYLOAD_SCHEMA,
                "backup_name": backup_name,
                "database": {
                    "format": "postgresql-custom",
                    "dump": "postgres/ananta.dump",
                    "catalog": "postgres/ananta.catalog.txt",
                    "image": (
                        "postgres:16.13@sha256:"
                        + "a" * 64
                    ),
                },
                "configuration": configuration,
                "bind_trees": {
                    "project_workspaces": "bind/project-workspaces",
                    "workflow_secrets": "bind/workflow-secrets",
                },
                "volumes": {
                    name: f"volumes/{name}.tar"
                    for name in ("hub-data", "alpha-data", "beta-data")
                },
                "generated_integrity": generated,
            }
        ),
        encoding="utf-8",
    )
    return payload


def _package(tmp_path: Path, backup_name: str) -> Path:
    package = tmp_path / backup_name
    package.mkdir()
    archive_name = f"{backup_name}.tar.zst.gpg"
    ciphertext = package / archive_name
    ciphertext.write_bytes(b"ciphertext")
    body = ciphertext.read_bytes()
    (package / "checksum.json").write_text(
        json.dumps(
            {
                "schema": BackupService.CHECKSUM_SCHEMA,
                "backup_name": backup_name,
                "archive": archive_name,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body),
                "encrypted": True,
            }
        ),
        encoding="utf-8",
    )
    return package


def test_restore_drill_extracts_and_verifies_without_docker_mutation(
    tmp_path: Path,
) -> None:
    backup_name = "ananta-backup-test"
    payload = _payload(tmp_path, backup_name)
    package = _package(tmp_path, backup_name)
    target = tmp_path / "restored"
    target.mkdir()
    postgres_drill = SuccessfulPostgresDrill()
    bind_verifier = SuccessfulBindVerifier()
    service = RestoreService(  # type: ignore[arg-type]
        PreparedPayloadArchive(payload),
        postgres_drill=postgres_drill,
        bind_verifier=bind_verifier,
    )

    result = service.run(RestoreRequest(package=package, target=target))

    assert result.target == target
    report = json.loads(
        (target / "RESTORE_VERIFIED.json").read_text(encoding="utf-8")
    )
    assert report["live_ananta_state_mutated"] is False
    assert report["ephemeral_postgres_container_created"] is True
    assert report["verification"]["worker_sqlite_integrity"] == "ok"
    assert report["verification"]["ephemeral_postgres_restore"] == "ok"
    assert report["verification"]["workflow_credentials"] == (
        "cryptographic_and_registration_binding_ok"
    )
    assert report["verification"]["ollama_models"] == "not_included"
    assert report["verification"]["origin_authenticity"] == (
        "not_verified_unsigned_openpgp_v1"
    )
    assert len(postgres_drill.dumps) == 1
    assert set(bind_verifier.bind_trees) == {
        "project_workspaces",
        "workflow_secrets",
    }
    assert (target / "restored-volumes" / "alpha-data" / "ananta.db").is_file()


def test_restore_dry_run_verifies_ciphertext_without_decrypting(
    tmp_path: Path,
) -> None:
    backup_name = "ananta-backup-test"
    package = _package(tmp_path, backup_name)

    class NoDecrypt:
        def decrypt_to_directory(self, encrypted: Path, destination: Path) -> None:
            raise AssertionError("dry-run must not decrypt")

    result = RestoreService(NoDecrypt()).run(  # type: ignore[arg-type]
        RestoreRequest(package=package, target=tmp_path / "new-target", dry_run=True)
    )

    assert result.dry_run is True
    assert not result.target.exists()


def test_restore_uses_one_private_ciphertext_snapshot_across_both_passes(
    tmp_path: Path,
) -> None:
    backup_name = "ananta-backup-test"
    payload = _payload(tmp_path, backup_name)
    package = _package(tmp_path, backup_name)
    source = package / f"{backup_name}.tar.zst.gpg"
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    class SourceMutatingArchive(PreparedPayloadArchive):
        def read_member_bytes(
            self,
            encrypted: Path,
            member_names: set[str],
            *,
            max_member_bytes: int = 1024 * 1024,
        ) -> dict[str, bytes]:
            result = super().read_member_bytes(
                encrypted,
                member_names,
                max_member_bytes=max_member_bytes,
            )
            source.write_bytes(b"changed after immutable snapshot")
            return result

    archive = SourceMutatingArchive(payload)
    target = tmp_path / "restored"
    result = RestoreService(
        archive,  # type: ignore[arg-type]
        postgres_drill=SuccessfulPostgresDrill(),
        bind_verifier=SuccessfulBindVerifier(),  # type: ignore[arg-type]
        staging_root=staging_root,
    ).run(RestoreRequest(package=package, target=target))

    assert result.target == target
    assert archive.metadata_paths == archive.decrypt_paths
    assert archive.metadata_paths[0] != source
    assert not archive.metadata_paths[0].exists()
    assert not any(staging_root.iterdir())


def test_restore_rejects_a_staged_ciphertext_changed_after_preflight(
    tmp_path: Path,
) -> None:
    backup_name = "ananta-backup-test"
    payload = _payload(tmp_path, backup_name)
    package = _package(tmp_path, backup_name)
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    class StagingMutatingArchive(PreparedPayloadArchive):
        def read_member_bytes(
            self,
            encrypted: Path,
            member_names: set[str],
            *,
            max_member_bytes: int = 1024 * 1024,
        ) -> dict[str, bytes]:
            result = super().read_member_bytes(
                encrypted,
                member_names,
                max_member_bytes=max_member_bytes,
            )
            encrypted.chmod(0o600)
            encrypted.write_bytes(b"changed staged ciphertext")
            return result

    archive = StagingMutatingArchive(payload)
    target = tmp_path / "restored"
    with pytest.raises(BackupError, match="changed during restore"):
        RestoreService(
            archive,  # type: ignore[arg-type]
            postgres_drill=SuccessfulPostgresDrill(),
            bind_verifier=SuccessfulBindVerifier(),  # type: ignore[arg-type]
            staging_root=staging_root,
        ).run(RestoreRequest(package=package, target=target))

    assert archive.decrypt_count == 0
    assert not target.exists()
    assert not any(staging_root.iterdir())


def test_restore_rejects_checksum_mismatch_before_decryption(tmp_path: Path) -> None:
    backup_name = "ananta-backup-test"
    package = _package(tmp_path, backup_name)
    archive = package / f"{backup_name}.tar.zst.gpg"
    archive.write_bytes(b"tampered")

    with pytest.raises(BackupError, match="checksum or size"):
        RestoreService().run(
            RestoreRequest(package=package, target=tmp_path / "new-target")
        )


def test_restore_policy_rejects_windows_interop_mount() -> None:
    with pytest.raises(BackupError, match="protected live/Windows root"):
        RestoreService()._reject_live_target(Path("/mnt/c/Users/test/Desktop/drill"))


def test_restore_rejects_recorded_live_target_before_plaintext_extraction(
    tmp_path: Path,
) -> None:
    backup_name = "ananta-backup-test"
    payload = _payload(tmp_path, backup_name)
    package = _package(tmp_path, backup_name)
    live_root = tmp_path / "live-workspaces"
    live_root.mkdir()
    target = live_root / "restore"
    archive = PreparedPayloadArchive(payload)
    service = RestoreService(
        archive,  # type: ignore[arg-type]
        postgres_drill=SuccessfulPostgresDrill(),
        bind_verifier=SuccessfulBindVerifier(),  # type: ignore[arg-type]
    )

    with pytest.raises(BackupError, match="bind root recorded"):
        service.run(RestoreRequest(package=package, target=target))

    assert archive.decrypt_count == 0
    assert not target.exists()
    assert not any(
        path.name.startswith(f".{target.name}.restore-")
        for path in live_root.iterdir()
    )
