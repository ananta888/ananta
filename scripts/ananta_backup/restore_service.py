"""Offline extraction and verification drill for encrypted backup packages."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from .archive import OpenPgpArchive, SafeTarExtractor
from .backup_service import BackupService
from .bind_verification import RestoredBindVerifier
from .errors import BackupError
from .filesystem import (
    copy_exclusive_and_hash,
    ensure_disjoint,
    fsync_directory,
    load_json_object,
    private_umask,
    remove_private_tree,
    require_absolute_directory,
    require_new_or_empty_target,
    require_private_wsl_directory,
    sha256_file,
    write_json_exclusive,
)


@dataclass(frozen=True)
class RestoreRequest:
    package: Path
    target: Path
    dry_run: bool = False


@dataclass(frozen=True)
class RestoreResult:
    target: Path
    backup_name: str
    sha256: str
    dry_run: bool


class PostgresRestoreDrill(Protocol):
    def verify(self, dump: Path) -> None:
        """Restore a dump into an isolated disposable PostgreSQL instance."""


class RestoreService:
    """Restores only to an isolated filesystem drill target."""

    VERIFIED_SCHEMA = "ananta.restore-drill.v1"
    WINDOWS_INTEROP_MOUNT = Path("/mnt")
    _POSTGRES_IMAGE = re.compile(
        r"^postgres:[A-Za-z0-9_.-]+(?:@sha256:[a-f0-9]{64})?$"
    )
    _PREFLIGHT_MEMBERS = {
        "backup.json",
        "configuration/compose.rendered.json",
    }

    def __init__(
        self,
        archive: OpenPgpArchive | None = None,
        *,
        postgres_drill: PostgresRestoreDrill | None = None,
        postgres_drill_factory: (
            Callable[[str], PostgresRestoreDrill] | None
        ) = None,
        bind_verifier: RestoredBindVerifier | None = None,
        forbidden_live_roots: Iterable[Path] = (),
        staging_root: Path = Path("/tmp"),
    ) -> None:
        self.archive = archive or OpenPgpArchive()
        self.postgres_drill = postgres_drill
        self.postgres_drill_factory = postgres_drill_factory
        self.bind_verifier = bind_verifier or RestoredBindVerifier()
        self.forbidden_live_roots = tuple(
            root.resolve(strict=False) for root in forbidden_live_roots
        )
        self.staging_root = staging_root

    def run(self, request: RestoreRequest) -> RestoreResult:
        with private_umask():
            return self._run_private(request)

    def _run_private(self, request: RestoreRequest) -> RestoreResult:
        package = require_absolute_directory(
            request.package, "Backup package", writable=False
        )
        target, target_existed = require_new_or_empty_target(
            request.target, "Restore target"
        )
        ensure_disjoint(package, target, ("Backup package", "restore target"))
        self._reject_live_target(target)
        manifest, encrypted, expected_digest, expected_size = (
            self._verify_package(package)
        )
        backup_name = self._required_text(manifest, "backup_name")
        staging_parent = require_private_wsl_directory(
            self.staging_root,
            "Restore ciphertext staging root",
        )
        staging: Path | None = Path(
            tempfile.mkdtemp(
                prefix=".ananta-restore-ciphertext-",
                dir=staging_parent,
            )
        )
        os.chmod(staging, 0o700)
        try:
            staged_encrypted = staging / encrypted.name
            digest, size = copy_exclusive_and_hash(
                encrypted,
                staged_encrypted,
            )
            if digest != expected_digest or size != expected_size:
                raise BackupError(
                    "Encrypted backup checksum or size does not match"
                )
            os.chmod(staged_encrypted, 0o400)
            fsync_directory(staging)

            if request.dry_run:
                return RestoreResult(target, backup_name, digest, True)

            preflight = self.archive.read_member_bytes(
                staged_encrypted,
                self._PREFLIGHT_MEMBERS,
            )
            self._verify_staged_ciphertext(
                staged_encrypted,
                expected_digest,
                expected_size,
            )
            self._verify_preflight_target(
                preflight,
                backup_name=backup_name,
                restore_target=target,
            )
            temporary: Path | None = Path(
                tempfile.mkdtemp(
                    prefix=f".{target.name}.restore-",
                    dir=target.parent,
                )
            )
            os.chmod(temporary, 0o700)
            try:
                self.archive.decrypt_to_directory(
                    staged_encrypted,
                    temporary,
                )
                self._verify_staged_ciphertext(
                    staged_encrypted,
                    expected_digest,
                    expected_size,
                )
                payload, models_verified = self._verify_payload(
                    temporary,
                    backup_name,
                    restore_target=target,
                )
                self._drill_volume_archives(temporary, payload)
                self._verify_postgres_dump(temporary, payload)
                write_json_exclusive(
                    temporary / "RESTORE_VERIFIED.json",
                    {
                        "schema": self.VERIFIED_SCHEMA,
                        "backup_name": backup_name,
                        "ciphertext_sha256": digest,
                        "live_ananta_state_mutated": False,
                        "ephemeral_postgres_container_created": True,
                        "verification": {
                            "encrypted_package_checksum": "ok",
                            "generated_payload_checksums": "ok",
                            "postgres_custom_dump": "ok",
                            "ephemeral_postgres_restore": "ok",
                            "volume_archives_safely_extracted": "ok",
                            "worker_sqlite_integrity": "ok",
                            "workflow_credentials": (
                                "cryptographic_and_registration_binding_ok"
                            ),
                            "ollama_models": (
                                "ok" if models_verified else "not_included"
                            ),
                            "origin_authenticity": (
                                "not_verified_unsigned_openpgp_v1"
                            ),
                        },
                    },
                )
                fsync_directory(temporary)
                if target_existed:
                    target.rmdir()
                os.replace(temporary, target)
                temporary = None
                fsync_directory(target.parent)
                return RestoreResult(target, backup_name, digest, False)
            finally:
                remove_private_tree(temporary)
        finally:
            remove_private_tree(staging)

    def _verify_preflight_target(
        self,
        metadata: dict[str, bytes],
        *,
        backup_name: str,
        restore_target: Path,
    ) -> None:
        payload = self._json_bytes_object(
            metadata.get("backup.json"),
            "encrypted payload manifest",
        )
        if (
            payload.get("schema") != BackupService.PAYLOAD_SCHEMA
            or payload.get("backup_name") != backup_name
        ):
            raise BackupError(
                "Encrypted payload preflight identity does not match package"
            )
        configuration = payload.get("configuration")
        if (
            not isinstance(configuration, dict)
            or configuration.get("rendered_compose")
            != "configuration/compose.rendered.json"
        ):
            raise BackupError(
                "Encrypted payload preflight configuration is invalid"
            )
        rendered = self._json_bytes_object(
            metadata.get("configuration/compose.rendered.json"),
            "encrypted rendered Compose configuration",
        )
        self._reject_rendered_compose_target(
            rendered,
            restore_target,
        )

    def _reject_live_target(self, target: Path) -> None:
        normalized = target.resolve(strict=False)
        protected_roots = (
            *self.forbidden_live_roots,
            self.WINDOWS_INTEROP_MOUNT,
        )
        for live_root in protected_roots:
            live_root = live_root.resolve(strict=False)
            try:
                common = os.path.commonpath((str(live_root), str(normalized)))
            except ValueError:
                continue
            if common == str(live_root):
                raise BackupError(
                    f"Restore target is inside a protected live/Windows root: {live_root}"
                )

    def _verify_package(
        self, package: Path
    ) -> tuple[dict[str, object], Path, str, int]:
        entries = sorted(item.name for item in package.iterdir())
        if "checksum.json" not in entries:
            raise BackupError("Backup package has no checksum.json")
        manifest = load_json_object(package / "checksum.json", "checksum manifest")
        if manifest.get("schema") != BackupService.CHECKSUM_SCHEMA:
            raise BackupError("Unsupported checksum manifest schema")
        if manifest.get("encrypted") is not True:
            raise BackupError("Backup package is not marked as encrypted")
        archive_name = self._required_text(manifest, "archive")
        if Path(archive_name).name != archive_name or not archive_name.endswith(
            ".tar.zst.gpg"
        ):
            raise BackupError("Checksum manifest contains an unsafe archive name")
        if entries != sorted(["checksum.json", archive_name]):
            raise BackupError("Backup package must contain only ciphertext and checksum")
        encrypted = package / archive_name
        digest = manifest.get("sha256")
        size = manifest.get("size_bytes")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or type(size) is not int
            or size < 1
        ):
            raise BackupError(
                "Checksum manifest has an invalid digest or size"
            )
        return manifest, encrypted, digest, size

    @staticmethod
    def _verify_staged_ciphertext(
        encrypted: Path,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        digest, size = sha256_file(encrypted)
        if digest != expected_digest or size != expected_size:
            raise BackupError(
                "Staged encrypted backup changed during restore"
            )

    def _verify_payload(
        self,
        root: Path,
        expected_backup_name: str,
        *,
        restore_target: Path,
    ) -> tuple[dict[str, object], bool]:
        payload = load_json_object(root / "backup.json", "payload manifest")
        if payload.get("schema") != BackupService.PAYLOAD_SCHEMA:
            raise BackupError("Unsupported payload manifest schema")
        if payload.get("backup_name") != expected_backup_name:
            raise BackupError("Payload backup name does not match package")
        integrity = payload.get("generated_integrity")
        if not isinstance(integrity, dict) or not integrity:
            raise BackupError("Payload has no generated integrity records")
        for relative_name, expected in integrity.items():
            if not isinstance(relative_name, str) or not isinstance(expected, dict):
                raise BackupError("Malformed generated integrity record")
            path = self._safe_payload_path(root, relative_name)
            digest, size = sha256_file(path)
            if expected.get("sha256") != digest or expected.get("size_bytes") != size:
                raise BackupError(f"Payload checksum does not match: {relative_name}")
        configuration = self._verify_configuration_paths(
            root,
            payload,
            integrity,
        )
        self._verify_rendered_compose_target(
            root,
            configuration,
            restore_target,
        )
        models_verified = self._verify_bind_tree_paths(root, payload)
        return payload, models_verified

    def _verify_configuration_paths(
        self,
        root: Path,
        payload: dict[str, object],
        integrity: dict[object, object],
    ) -> dict[str, str]:
        configuration = payload.get("configuration")
        required = {
            "environment",
            "rendered_compose",
            "model_profiles",
            "model_routing",
            "git_state",
        }
        if not isinstance(configuration, dict) or set(configuration) != required:
            raise BackupError("Payload reproducibility configuration is incomplete")
        normalized: dict[str, str] = {}
        for label in sorted(required):
            relative = configuration.get(label)
            if not isinstance(relative, str) or relative not in integrity:
                raise BackupError(f"Configuration integrity is missing: {label}")
            self._safe_payload_path(root, relative)
            normalized[label] = relative
        return normalized

    def _verify_rendered_compose_target(
        self,
        root: Path,
        configuration: dict[str, str],
        restore_target: Path,
    ) -> None:
        rendered = load_json_object(
            self._safe_payload_path(
                root,
                configuration["rendered_compose"],
            ),
            "rendered Compose configuration",
        )
        self._reject_rendered_compose_target(rendered, restore_target)

    @staticmethod
    def _reject_rendered_compose_target(
        rendered: dict[str, object],
        restore_target: Path,
    ) -> None:
        services = rendered.get("services")
        if not isinstance(services, dict):
            raise BackupError(
                "Rendered Compose configuration has no services"
            )
        live_roots: list[Path] = []
        for service in services.values():
            if not isinstance(service, dict):
                raise BackupError(
                    "Rendered Compose service configuration is invalid"
                )
            volumes = service.get("volumes", [])
            if not isinstance(volumes, list):
                raise BackupError(
                    "Rendered Compose volume configuration is invalid"
                )
            for mount in volumes:
                if not isinstance(mount, dict) or mount.get("type") != "bind":
                    continue
                source = mount.get("source")
                if not isinstance(source, str) or not Path(source).is_absolute():
                    raise BackupError(
                        "Rendered Compose bind source is invalid"
                    )
                live_roots.append(Path(source))
        normalized = restore_target.resolve(strict=False)
        for live_root in live_roots:
            protected = live_root.resolve(strict=False)
            try:
                common = os.path.commonpath(
                    (str(protected), str(normalized))
                )
            except ValueError:
                continue
            if common == str(protected):
                raise BackupError(
                    "Restore target is inside a bind root recorded by the backup"
                )

    @staticmethod
    def _json_bytes_object(
        payload: bytes | None,
        label: str,
    ) -> dict[str, object]:
        if payload is None or len(payload) > 1024 * 1024:
            raise BackupError(f"{label} is missing or too large")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"{label} is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise BackupError(f"{label} must be an object")
        return decoded

    def _verify_bind_tree_paths(
        self, root: Path, payload: dict[str, object]
    ) -> bool:
        bind_trees = payload.get("bind_trees")
        if not isinstance(bind_trees, dict):
            raise BackupError("Payload bind-tree map is missing")
        required = {"project_workspaces", "workflow_secrets"}
        if not required.issubset(bind_trees) or not set(bind_trees).issubset(
            required | {"ollama_models"}
        ):
            raise BackupError("Payload bind-tree map is incomplete")
        restored: dict[str, Path] = {}
        for label, relative in bind_trees.items():
            if not isinstance(relative, str):
                raise BackupError(f"Invalid bind-tree path: {label}")
            path = self._safe_payload_path(root, relative)
            if not path.is_dir() or path.is_symlink():
                raise BackupError(f"Bind-tree restore path is not a directory: {label}")
            restored[str(label)] = path
        return self.bind_verifier.verify(restored)

    def _drill_volume_archives(
        self, root: Path, payload: dict[str, object]
    ) -> None:
        volumes = payload.get("volumes")
        if not isinstance(volumes, dict):
            raise BackupError("Payload volume map is missing")
        expected_names = {"hub-data", "alpha-data", "beta-data"}
        if set(volumes) != expected_names:
            raise BackupError("Payload volume map is incomplete")
        drill_root = root / "restored-volumes"
        drill_root.mkdir(mode=0o700)
        for logical_name in sorted(expected_names):
            relative = volumes.get(logical_name)
            if not isinstance(relative, str):
                raise BackupError(f"Invalid volume archive path: {logical_name}")
            archive_path = self._safe_payload_path(root, relative)
            destination = drill_root / logical_name
            destination.mkdir(mode=0o700)
            SafeTarExtractor.extract_file(archive_path, destination)
            if logical_name in {"alpha-data", "beta-data"}:
                self._verify_sqlite(destination / "ananta.db", logical_name)

    @staticmethod
    def _verify_sqlite(path: Path, logical_name: str) -> None:
        if not path.is_file() or path.is_symlink():
            raise BackupError(f"{logical_name} has no restorable ananta.db")
        uri = f"file:{path.as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise BackupError(f"{logical_name} SQLite verification failed: {exc}") from exc
        if result != ("ok",):
            raise BackupError(f"{logical_name} SQLite integrity check failed")

    def _verify_postgres_dump(
        self, root: Path, payload: dict[str, object]
    ) -> None:
        database = payload.get("database")
        if not isinstance(database, dict) or database.get("format") != "postgresql-custom":
            raise BackupError("Payload PostgreSQL metadata is invalid")
        dump_name = database.get("dump")
        catalog_name = database.get("catalog")
        if not isinstance(dump_name, str) or not isinstance(catalog_name, str):
            raise BackupError("Payload PostgreSQL paths are invalid")
        image = database.get("image")
        if not isinstance(image, str) or not self._POSTGRES_IMAGE.fullmatch(
            image
        ):
            raise BackupError("Payload PostgreSQL image is invalid")
        dump = self._safe_payload_path(root, dump_name)
        catalog = self._safe_payload_path(root, catalog_name)
        try:
            with dump.open("rb") as source:
                header = source.read(5)
            if header != b"PGDMP" or not catalog.read_text(encoding="utf-8").strip():
                raise BackupError("PostgreSQL restore drill validation failed")
        except (OSError, UnicodeError) as exc:
            raise BackupError(f"Cannot validate PostgreSQL dump: {exc}") from exc
        postgres_drill = self.postgres_drill
        if postgres_drill is None and self.postgres_drill_factory is not None:
            postgres_drill = self.postgres_drill_factory(image)
        if postgres_drill is None:
            raise BackupError("No isolated PostgreSQL restore drill is configured")
        postgres_drill.verify(dump)

    @staticmethod
    def _safe_payload_path(root: Path, relative_name: str) -> Path:
        relative = Path(relative_name)
        if (
            not relative_name
            or relative.is_absolute()
            or ".." in relative.parts
            or "\x00" in relative_name
        ):
            raise BackupError(f"Unsafe payload path: {relative_name!r}")
        candidate = root.joinpath(*relative.parts)
        try:
            common = os.path.commonpath(
                (str(root.resolve(strict=True)), str(candidate.resolve(strict=True)))
            )
        except (OSError, ValueError) as exc:
            raise BackupError(f"Payload path does not exist: {relative_name}") from exc
        if common != str(root.resolve(strict=True)):
            raise BackupError(f"Payload path leaves restore target: {relative_name}")
        return candidate

    @staticmethod
    def _required_text(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise BackupError(f"Manifest field is missing: {key}")
        return value
