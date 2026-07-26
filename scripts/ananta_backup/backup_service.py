"""Application service for atomic encrypted Ananta backups."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .archive import GpgRecipient, OpenPgpArchive, TreePlan
from .compose import ComposeAdapter, ComposeLayout
from .errors import BackupError
from .filesystem import (
    copy_exclusive,
    ensure_disjoint,
    fsync_directory,
    private_umask,
    rename_directory_no_replace,
    remove_private_tree,
    require_private_wsl_directory,
    require_absolute_directory,
    sha256_file,
    write_json_exclusive,
)
from .windows import WindowsDesktopPolicy

_BACKUP_NAME = re.compile(r"^ananta-backup-[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


@dataclass(frozen=True)
class BackupRequest:
    wsl_target: Path
    windows_target: Path
    recipient_file: Path
    backup_name: str | None = None
    include_ollama_models: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class BackupResult:
    backup_name: str
    wsl_package: Path
    windows_package: Path
    sha256: str | None
    size_bytes: int | None
    dry_run: bool


class BackupService:
    """Coordinates adapters without moving orchestration into a worker."""

    CHECKSUM_SCHEMA = "ananta.encrypted-backup-checksum.v1"
    PAYLOAD_SCHEMA = "ananta.backup-payload.v1"

    def __init__(
        self,
        compose: ComposeAdapter,
        archive: OpenPgpArchive | None = None,
        windows_policy: WindowsDesktopPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.compose = compose
        self.archive = archive or OpenPgpArchive()
        self.windows_policy = windows_policy or WindowsDesktopPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, request: BackupRequest) -> BackupResult:
        with private_umask():
            return self._run_private(request)

    def _run_private(self, request: BackupRequest) -> BackupResult:
        wsl_target = require_private_wsl_directory(
            request.wsl_target,
            "WSL target",
        )
        windows_target = require_absolute_directory(
            request.windows_target, "Windows target"
        )
        self.windows_policy.validate(windows_target)
        if wsl_target == windows_target:
            raise BackupError("WSL and Windows targets must be different directories")

        backup_name = request.backup_name or self._default_name()
        if not _BACKUP_NAME.fullmatch(backup_name):
            raise BackupError(
                "Backup name must start with 'ananta-backup-' and contain safe characters"
            )
        wsl_package = wsl_target / backup_name
        windows_package = windows_target / backup_name
        if wsl_package.exists() or windows_package.exists():
            raise BackupError(f"Backup package already exists: {backup_name}")

        recipient = GpgRecipient.from_file(request.recipient_file)
        self.archive.verify_public_recipient(recipient)
        layout = self.compose.load_layout()
        self._validate_target_layout(wsl_target, layout)
        self.compose.preflight(layout)
        # Secrets are always inventoried, even for dry-runs. This is the
        # fail-closed readability gate and cannot be disabled.
        self._capture_external_sources(
            layout, include_ollama_models=request.include_ollama_models
        )

        if request.dry_run:
            return BackupResult(
                backup_name,
                wsl_package,
                windows_package,
                None,
                None,
                True,
            )

        work_directory: Path | None = None
        delivery_directory: Path | None = None
        try:
            work_directory = Path(
                tempfile.mkdtemp(prefix=f".{backup_name}.work-", dir=wsl_target)
            )
            delivery_directory = Path(
                tempfile.mkdtemp(prefix=f".{backup_name}.delivery-", dir=wsl_target)
            )
            os.chmod(work_directory, 0o700)
            os.chmod(delivery_directory, 0o700)
            archive_name = f"{backup_name}.tar.zst.gpg"
            encrypted_path = delivery_directory / archive_name

            with self.compose.paused_agents():
                self.compose.assert_quiescent(layout)
                # Capture again after writers have stopped so workspaces and
                # credentials have a stable inventory for the streamed tar.
                source_plans = self._capture_external_sources(
                    layout, include_ollama_models=request.include_ollama_models
                )
                self._collect_generated_payload(
                    layout,
                    work_directory,
                    backup_name,
                    include_ollama_models=request.include_ollama_models,
                )
                payload_plan = TreePlan.capture(
                    "generated backup payload", work_directory, None
                )
                self.archive.encrypt_trees(
                    [payload_plan, *source_plans], recipient, encrypted_path
                )

            digest, size = sha256_file(encrypted_path)
            checksum = self._checksum_manifest(backup_name, archive_name, digest, size)
            write_json_exclusive(delivery_directory / "checksum.json", checksum)
            fsync_directory(delivery_directory)
            rename_directory_no_replace(delivery_directory, wsl_package)
            delivery_directory = None
            fsync_directory(wsl_target)

            try:
                self._publish_windows_copy(
                    wsl_package,
                    windows_target,
                    windows_package,
                    archive_name,
                    checksum,
                    digest,
                    size,
                )
            except BaseException as exc:
                try:
                    remove_private_tree(wsl_package)
                    fsync_directory(wsl_target)
                except OSError as cleanup_exc:
                    exc.add_note(
                        "The failed two-target publication could not remove "
                        f"its WSL package: {cleanup_exc}"
                    )
                raise
            return BackupResult(
                backup_name,
                wsl_package,
                windows_package,
                digest,
                size,
                False,
            )
        finally:
            remove_private_tree(work_directory)
            remove_private_tree(delivery_directory)

    @staticmethod
    def _validate_target_layout(
        wsl_target: Path,
        layout: ComposeLayout,
    ) -> None:
        for label, live_root in (
            ("Ollama root", layout.ollama_root),
            ("workflow secret root", layout.workflow_secret_root),
            ("project workspace root", layout.workspace_root),
        ):
            ensure_disjoint(
                wsl_target,
                live_root,
                ("WSL target", label),
            )

    def _capture_external_sources(
        self, layout: ComposeLayout, *, include_ollama_models: bool
    ) -> list[TreePlan]:
        # The strict secret plan opens every regular file and rejects links. A
        # missing/unreadable credential therefore aborts before any snapshot.
        plans = [
            TreePlan.capture(
                "workflow secret tree",
                layout.workflow_secret_root,
                "bind/workflow-secrets",
                strict_files=True,
            ),
            TreePlan.capture(
                "project workspace tree",
                layout.workspace_root,
                "bind/project-workspaces",
                strict_files=False,
            ),
        ]
        if include_ollama_models:
            plans.append(
                TreePlan.capture(
                    "Ollama model tree",
                    layout.ollama_root / "models",
                    "bind/ollama/models",
                    strict_files=False,
                )
            )
        return plans

    def _collect_generated_payload(
        self,
        layout: ComposeLayout,
        work_directory: Path,
        backup_name: str,
        *,
        include_ollama_models: bool,
    ) -> None:
        postgres_directory = work_directory / "postgres"
        volume_directory = work_directory / "volumes"
        postgres_directory.mkdir(mode=0o700)
        volume_directory.mkdir(mode=0o700)
        configuration = self.compose.capture_reproducibility(
            layout, work_directory / "configuration"
        )
        database_dump = postgres_directory / "ananta.dump"
        catalog = self.compose.dump_postgres(layout, database_dump)

        volume_files: dict[str, str] = {}
        for logical_name in ("hub-data", "alpha-data", "beta-data"):
            exported = self.compose.export_volume(
                layout, logical_name, volume_directory
            )
            if logical_name in {"alpha-data", "beta-data"}:
                self.compose.verify_worker_sqlite_archive(
                    exported,
                    logical_name,
                )
            volume_files[logical_name] = f"volumes/{exported.name}"

        generated_files = [
            path
            for path in work_directory.rglob("*")
            if path.is_file() and not path.is_symlink()
        ]
        generated_integrity: dict[str, dict[str, object]] = {}
        for path in generated_files:
            digest, size = sha256_file(path)
            relative = path.relative_to(work_directory).as_posix()
            generated_integrity[relative] = {"sha256": digest, "size_bytes": size}

        bind_trees = {
            "project_workspaces": "bind/project-workspaces",
            "workflow_secrets": "bind/workflow-secrets",
        }
        if include_ollama_models:
            bind_trees["ollama_models"] = "bind/ollama/models"

        write_json_exclusive(
            work_directory / "backup.json",
            {
                "schema": self.PAYLOAD_SCHEMA,
                "backup_name": backup_name,
                "created_utc": self.clock()
                .astimezone(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "compose_project": layout.project_name,
                "configuration": configuration,
                "database": {
                    "format": "postgresql-custom",
                    "dump": "postgres/ananta.dump",
                    "catalog": "postgres/ananta.catalog.txt",
                    "image": layout.postgres_image,
                },
                "volumes": volume_files,
                "bind_trees": bind_trees,
                "generated_integrity": generated_integrity,
            },
        )

    def _publish_windows_copy(
        self,
        wsl_package: Path,
        windows_target: Path,
        windows_package: Path,
        archive_name: str,
        checksum: dict[str, object],
        expected_digest: str,
        expected_size: int,
    ) -> None:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{windows_package.name}.delivery-", dir=windows_target)
        )
        published = False
        try:
            os.chmod(temporary, 0o700)
            copy_exclusive(wsl_package / archive_name, temporary / archive_name)
            copied_digest, copied_size = sha256_file(temporary / archive_name)
            if (copied_digest, copied_size) != (expected_digest, expected_size):
                raise BackupError("Windows ciphertext copy failed checksum verification")
            write_json_exclusive(temporary / "checksum.json", checksum)
            fsync_directory(temporary)
            self.windows_policy.publish_no_replace(
                temporary,
                windows_package,
            )
            published = True
            fsync_directory(windows_target)
        except BaseException as exc:
            cleanup_target = windows_package if published else temporary
            try:
                remove_private_tree(cleanup_target)
                fsync_directory(windows_target)
            except OSError as cleanup_error:
                exc.add_note(
                    "Windows publication cleanup also failed: "
                    f"{cleanup_error}"
                )
            raise

    def _default_name(self) -> str:
        timestamp = (
            self.clock()
            .astimezone(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y%m%dT%H%M%SZ")
        )
        return f"ananta-backup-{timestamp}"

    @classmethod
    def _checksum_manifest(
        cls,
        backup_name: str,
        archive_name: str,
        digest: str,
        size: int,
    ) -> dict[str, object]:
        return {
            "schema": cls.CHECKSUM_SCHEMA,
            "backup_name": backup_name,
            "archive": archive_name,
            "sha256": digest,
            "size_bytes": size,
            "encrypted": True,
        }
