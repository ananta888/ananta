"""Docker Compose infrastructure adapter for consistent application snapshots."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import stat
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .archive import SafeTarExtractor
from .errors import BackupError
from .filesystem import write_json_exclusive
from .process import CommandRunner, SystemCommandRunner

_SAFE_DOCKER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")


@dataclass(frozen=True)
class ComposeLayout:
    """Resolved sources needed for one self-contained backup."""

    project_name: str
    runtime_image: str
    runtime_user: str
    postgres_image: str
    postgres_user: str
    postgres_database: str
    named_volumes: Mapping[str, str]
    ollama_root: Path
    workflow_secret_root: Path
    workspace_root: Path
    model_profiles_file: Path
    model_routing_file: Path


class ComposeAdapter:
    """Read-only discovery plus narrowly scoped snapshot commands."""

    REQUIRED_RUNNING_SERVICES = (
        "ai-agent-hub",
        "ai-agent-alpha",
        "ai-agent-beta",
        "postgres",
    )
    PAUSED_SERVICES = ("ai-agent-hub", "ai-agent-alpha", "ai-agent-beta")
    DATA_VOLUME_TARGETS = {
        "hub-data": ("ai-agent-hub", "/app/data"),
        "alpha-data": ("ai-agent-alpha", "/app/data"),
        "beta-data": ("ai-agent-beta", "/app/data"),
    }
    EXECUTING_TASK_STATUSES = (
        "assigned",
        "delegated",
        "in_progress",
        "proposing",
        "running",
        "uncertain",
    )

    def __init__(
        self,
        compose_file: Path,
        env_file: Path,
        runner: CommandRunner | None = None,
    ) -> None:
        self.compose_file = compose_file.resolve(strict=True)
        self.env_file = env_file.resolve(strict=True)
        self.runner = runner or SystemCommandRunner()

    @property
    def _base(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.env_file),
            "--file",
            str(self.compose_file),
        ]

    def load_layout(self) -> ComposeLayout:
        raw = self.runner.capture([*self._base, "config", "--format", "json"])
        try:
            config = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError("Docker Compose returned invalid configuration JSON") from exc
        if not isinstance(config, dict):
            raise BackupError("Docker Compose configuration must be an object")

        project_name = self._safe_name(config.get("name"), "Compose project")
        services = self._mapping(config.get("services"), "services")
        volumes = self._mapping(config.get("volumes"), "volumes")
        hub = self._mapping(services.get("ai-agent-hub"), "ai-agent-hub")
        postgres = self._mapping(services.get("postgres"), "postgres")

        runtime_image = self._required_text(hub.get("image"), "Hub runtime image")
        runtime_user = self._required_text(hub.get("user"), "Hub runtime user")
        if not _NUMERIC_USER.fullmatch(runtime_user):
            raise BackupError("Hub runtime user must be an explicit non-root UID:GID")

        postgres_environment = self._mapping(
            postgres.get("environment"), "PostgreSQL environment"
        )
        postgres_user = self._required_text(
            postgres_environment.get("POSTGRES_USER"), "POSTGRES_USER"
        )
        postgres_database = self._required_text(
            postgres_environment.get("POSTGRES_DB"), "POSTGRES_DB"
        )
        postgres_image = self._required_text(
            postgres.get("image"), "PostgreSQL runtime image"
        )
        hub_environment = self._mapping(hub.get("environment"), "Hub environment")
        model_profiles_file = self._bind_file_source(
            hub,
            self._required_text(
                hub_environment.get("MODEL_PROFILES_PATH"), "MODEL_PROFILES_PATH"
            ),
        )
        model_routing_file = self._bind_file_source(
            hub,
            self._required_text(
                hub_environment.get("MODEL_ROUTING_PATH"), "MODEL_ROUTING_PATH"
            ),
        )

        named_volumes: dict[str, str] = {}
        for logical_name, (service_name, target) in self.DATA_VOLUME_TARGETS.items():
            service = self._mapping(services.get(service_name), service_name)
            source = self._volume_source(service, target, "volume")
            volume_config = self._mapping(volumes.get(source), f"volume {source}")
            actual_name = self._safe_name(
                volume_config.get("name"), f"volume {logical_name}"
            )
            named_volumes[logical_name] = actual_name

        ollama_root = self._bind_source(services, "ollama", "/root/.ollama")
        workspace_root = self._bind_source(
            services, "ai-agent-hub", "/project-workspaces"
        )
        workflow_secret_root = self._bind_source(
            services, "workflow-keyring-bootstrap", "/run/ananta-dev-workflow"
        )

        return ComposeLayout(
            project_name=project_name,
            runtime_image=runtime_image,
            runtime_user=runtime_user,
            postgres_image=postgres_image,
            postgres_user=postgres_user,
            postgres_database=postgres_database,
            named_volumes=named_volumes,
            ollama_root=ollama_root,
            workflow_secret_root=workflow_secret_root,
            workspace_root=workspace_root,
            model_profiles_file=model_profiles_file,
            model_routing_file=model_routing_file,
        )

    def preflight(self, layout: ComposeLayout) -> None:
        running = set(
            self.runner.capture(
                [*self._base, "ps", "--status", "running", "--services"]
            )
            .decode("utf-8", errors="strict")
            .splitlines()
        )
        missing = sorted(set(self.REQUIRED_RUNNING_SERVICES) - running)
        if missing:
            raise BackupError(
                "Required Compose services are not running: " + ", ".join(missing)
            )
        for actual_name in layout.named_volumes.values():
            inspected = (
                self.runner.capture(
                    ["docker", "volume", "inspect", "--format", "{{.Name}}", actual_name]
                )
                .decode("utf-8", errors="strict")
                .strip()
            )
            if inspected != actual_name:
                raise BackupError(f"Docker volume identity mismatch: {actual_name}")

    @contextmanager
    def paused_agents(self) -> Iterator[None]:
        paused: list[str] = []
        try:
            for service in self.PAUSED_SERVICES:
                self.runner.run([*self._base, "pause", service])
                paused.append(service)
            yield
        finally:
            cleanup_errors: list[str] = []
            for service in reversed(paused):
                try:
                    self.runner.run([*self._base, "unpause", service])
                except BackupError as exc:
                    cleanup_errors.append(f"{service}: {exc}")
            if cleanup_errors:
                raise BackupError(
                    "Failed to resume one or more Ananta services: "
                    + "; ".join(cleanup_errors)
                )

    def assert_quiescent(self, layout: ComposeLayout) -> None:
        """Fail if the Hub still owns tasks which may be executing."""

        statuses = ", ".join(
            f"'{status}'" for status in self.EXECUTING_TASK_STATUSES
        )
        query = (
            "SET statement_timeout = '10s'; "
            "SELECT count(*) FROM tasks "
            f"WHERE lower(coalesce(status, '')) IN ({statuses});"
        )
        raw = self.runner.capture(
            [
                *self._base,
                "exec",
                "--no-TTY",
                "postgres",
                "psql",
                "--set",
                "ON_ERROR_STOP=1",
                "--username",
                layout.postgres_user,
                "--dbname",
                layout.postgres_database,
                "--tuples-only",
                "--no-align",
                "--command",
                query,
            ]
        )
        lines = [
            line.strip()
            for line in raw.decode("ascii", errors="strict").splitlines()
            if line.strip() and line.strip() != "SET"
        ]
        if len(lines) != 1 or not lines[0].isdigit():
            raise BackupError("Cannot verify the Hub task quiescence gate")
        if int(lines[0]) != 0:
            raise BackupError(
                "Backup requires zero potentially executing Hub tasks "
                f"(statuses: {', '.join(self.EXECUTING_TASK_STATUSES)})"
            )

    def dump_postgres(self, layout: ComposeLayout, destination: Path) -> Path:
        command = [
            *self._base,
            "exec",
            "--no-TTY",
            "postgres",
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--username",
            layout.postgres_user,
            "--dbname",
            layout.postgres_database,
        ]
        self.runner.to_file(command, destination)
        try:
            with destination.open("rb") as dump:
                header = dump.read(5)
            if header != b"PGDMP":
                raise BackupError("PostgreSQL dump does not have custom-format header")
        except OSError as exc:
            raise BackupError(f"Cannot validate PostgreSQL dump: {exc}") from exc

        catalog = self.runner.from_file(
            [*self._base, "exec", "--no-TTY", "postgres", "pg_restore", "--list"],
            destination,
        )
        if not catalog.strip():
            raise BackupError("pg_restore returned an empty catalog")
        catalog_path = destination.with_suffix(".catalog.txt")
        descriptor = catalog_path.open("xb")
        with descriptor:
            descriptor.write(catalog)
            descriptor.flush()
        return catalog_path

    def capture_reproducibility(
        self, layout: ComposeLayout, destination: Path
    ) -> dict[str, str]:
        """Capture secret configuration and source state inside the encrypted payload."""

        destination.mkdir(mode=0o700)
        env_copy = destination / ".env"
        profile_copy = destination / "model_profiles.yaml"
        routing_copy = destination / "model_routing.json"
        self._copy_strict_regular(self.env_file, env_copy, ".env")
        self._copy_strict_regular(
            layout.model_profiles_file, profile_copy, "model profile"
        )
        self._copy_strict_regular(
            layout.model_routing_file, routing_copy, "model routing"
        )

        rendered = destination / "compose.rendered.json"
        self.runner.to_file(
            [*self._base, "config", "--format", "json"],
            rendered,
        )
        try:
            parsed = json.loads(rendered.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"Rendered Compose configuration is invalid: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BackupError("Rendered Compose configuration is not an object")

        repository_root = self.compose_file.parents[2]
        commit = (
            self.runner.capture(
                ["git", "-C", str(repository_root), "rev-parse", "HEAD"]
            )
            .decode("ascii", errors="strict")
            .strip()
        )
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
            raise BackupError("Git returned an invalid commit identity")
        dirty_lines = (
            self.runner.capture(
                [
                    "git",
                    "-C",
                    str(repository_root),
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                ]
            )
            .decode("utf-8", errors="strict")
            .splitlines()
        )
        write_json_exclusive(
            destination / "git-state.json",
            {
                "commit": commit,
                "dirty": bool(dirty_lines),
                "porcelain_v1": dirty_lines,
            },
        )
        return {
            "environment": "configuration/.env",
            "rendered_compose": "configuration/compose.rendered.json",
            "model_profiles": "configuration/model_profiles.yaml",
            "model_routing": "configuration/model_routing.json",
            "git_state": "configuration/git-state.json",
        }

    @staticmethod
    def verify_worker_sqlite_archive(
        archive_path: Path,
        logical_name: str,
    ) -> None:
        """Verify the exact paused-volume archive without touching live SQLite.

        A read-only SQLite connection against the live WAL volume cannot acquire
        its shared-memory lock while the owning Worker container is paused.
        Verification therefore runs against a safely extracted copy of the
        archive that will actually be encrypted.
        """

        if logical_name not in {"alpha-data", "beta-data"}:
            raise BackupError("Worker SQLite verification target is invalid")
        try:
            with tempfile.TemporaryDirectory(
                prefix=f".{logical_name}.verify-",
                dir=archive_path.parent,
            ) as temporary_name:
                temporary = Path(temporary_name)
                os.chmod(temporary, 0o700)
                SafeTarExtractor.extract_file(archive_path, temporary)
                database = temporary / "ananta.db"
                if not database.is_file() or database.is_symlink():
                    raise BackupError(
                        f"{logical_name} archive has no regular ananta.db"
                    )
                connection = sqlite3.connect(
                    f"file:{database.as_posix()}?mode=ro",
                    uri=True,
                )
                try:
                    result = connection.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()
                finally:
                    connection.close()
        except sqlite3.Error as exc:
            raise BackupError(
                f"{logical_name} archive SQLite verification failed: {exc}"
            ) from exc
        if result != ("ok",):
            raise BackupError(
                f"{logical_name} archive SQLite integrity check failed"
            )

    def export_volume(
        self,
        layout: ComposeLayout,
        logical_name: str,
        destination_directory: Path,
    ) -> Path:
        actual_name = layout.named_volumes[logical_name]
        self._validate_mount_path(destination_directory)
        output_name = f"{logical_name}.tar"
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            layout.runtime_user,
            "--mount",
            f"type=volume,source={actual_name},target=/source,readonly",
            "--mount",
            f"type=bind,source={destination_directory},target=/backup",
            "--entrypoint",
            "tar",
            layout.runtime_image,
            "--create",
            "--file",
            f"/backup/{output_name}",
            "--format=pax",
            "--numeric-owner",
            "--one-file-system",
            "--hard-dereference",
            "--directory=/source",
            ".",
        ]
        self.runner.run(command)
        output_path = destination_directory / output_name
        self._validate_inner_tar(output_path)
        return output_path

    @staticmethod
    def _validate_inner_tar(path: Path) -> None:
        try:
            with tarfile.open(path, mode="r:") as archive:
                for member in archive:
                    parts = Path(member.name).parts
                    if member.name.startswith("/") or ".." in parts:
                        raise BackupError(
                            f"Unsafe path in volume archive {path.name}: {member.name}"
                        )
                    if not (
                        member.isdir()
                        or member.isreg()
                        or member.issym()
                    ):
                        raise BackupError(
                            f"Special entry in volume archive {path.name}: {member.name}"
                        )
                    if member.issym():
                        link = Path(member.name).parent / member.linkname
                        if member.linkname.startswith("/") or ".." in link.parts:
                            raise BackupError(
                                f"Unsafe symlink in volume archive {path.name}: "
                                f"{member.name}"
                            )
        except (OSError, tarfile.TarError) as exc:
            raise BackupError(f"Invalid volume archive {path.name}: {exc}") from exc

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, dict):
            raise BackupError(f"Compose {label} must be an object")
        return value

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise BackupError(f"{label} is missing")
        return value.strip()

    @classmethod
    def _safe_name(cls, value: object, label: str) -> str:
        text = cls._required_text(value, label)
        if not _SAFE_DOCKER_NAME.fullmatch(text):
            raise BackupError(f"{label} has an unsafe Docker identifier")
        return text

    @classmethod
    def _volume_source(
        cls, service: Mapping[str, object], target: str, expected_type: str
    ) -> str:
        mounts = service.get("volumes")
        if not isinstance(mounts, list):
            raise BackupError(f"Service has no volume for {target}")
        matches = [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and mount.get("target") == target
            and mount.get("type") == expected_type
        ]
        if len(matches) != 1:
            raise BackupError(f"Expected exactly one {expected_type} mount at {target}")
        return cls._required_text(matches[0].get("source"), f"source for {target}")

    @classmethod
    def _bind_source(
        cls, services: Mapping[str, object], service_name: str, target: str
    ) -> Path:
        service = cls._mapping(services.get(service_name), service_name)
        source = Path(cls._volume_source(service, target, "bind"))
        if not source.is_absolute():
            raise BackupError(f"Compose bind source for {target} is not absolute")
        # Existence is enforced by the component which actually consumes the
        # source. This keeps the optional Ollama model tree optional while
        # workflow secrets and workspaces still fail closed in TreePlan.
        return source.resolve(strict=False)

    @classmethod
    def _bind_file_source(
        cls, service: Mapping[str, object], container_path: str
    ) -> Path:
        requested = Path(container_path)
        if not requested.is_absolute():
            raise BackupError(f"Container configuration path is not absolute: {container_path}")
        mounts = service.get("volumes")
        if not isinstance(mounts, list):
            raise BackupError(f"No bind mount contains {container_path}")
        candidates: list[tuple[int, Path]] = []
        for mount in mounts:
            if not isinstance(mount, dict) or mount.get("type") != "bind":
                continue
            target_value = mount.get("target")
            source_value = mount.get("source")
            if not isinstance(target_value, str) or not isinstance(source_value, str):
                continue
            target = Path(target_value)
            try:
                relative = requested.relative_to(target)
            except ValueError:
                continue
            candidates.append((len(target.parts), Path(source_value) / relative))
        if not candidates:
            raise BackupError(f"No bind mount contains {container_path}")
        source = max(candidates, key=lambda candidate: candidate[0])[1]
        return source.resolve(strict=True)

    @staticmethod
    def _copy_strict_regular(source: Path, destination: Path, label: str) -> None:
        try:
            metadata = source.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_ISLNK(metadata.st_mode)
            ):
                raise BackupError(f"{label} must be a non-linked regular file")
            input_descriptor = os.open(
                source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                output_descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except Exception:
                os.close(input_descriptor)
                raise
            with os.fdopen(input_descriptor, "rb") as input_file, os.fdopen(
                output_descriptor, "wb"
            ) as output_file:
                opened_metadata = os.fstat(input_file.fileno())
                initial_signature = (
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_dev,
                    metadata.st_ino,
                )
                opened_signature = (
                    opened_metadata.st_mode,
                    opened_metadata.st_size,
                    opened_metadata.st_mtime_ns,
                    opened_metadata.st_dev,
                    opened_metadata.st_ino,
                )
                if opened_signature != initial_signature:
                    raise BackupError(
                        f"{label} changed while it was being opened"
                    )
                for block in iter(lambda: input_file.read(1024 * 1024), b""):
                    output_file.write(block)
                closed_signature = os.fstat(input_file.fileno())
                if (
                    closed_signature.st_mode,
                    closed_signature.st_size,
                    closed_signature.st_mtime_ns,
                    closed_signature.st_dev,
                    closed_signature.st_ino,
                ) != initial_signature:
                    raise BackupError(
                        f"{label} changed while it was being captured"
                    )
                output_file.flush()
                os.fsync(output_file.fileno())
            final_metadata = source.lstat()
            final_signature = (
                final_metadata.st_mode,
                final_metadata.st_size,
                final_metadata.st_mtime_ns,
                final_metadata.st_dev,
                final_metadata.st_ino,
            )
            if initial_signature != final_signature:
                destination.unlink(missing_ok=True)
                raise BackupError(f"{label} changed while it was being captured")
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise BackupError(f"Cannot capture {label}: {exc}") from exc

    @staticmethod
    def _validate_mount_path(path: Path) -> None:
        if "," in str(path) or "\n" in str(path):
            raise BackupError(
                "Temporary backup path cannot contain a comma or newline for Docker mount"
            )


class IsolatedPostgresRestoreDrill:
    """Verify pg_restore in an ephemeral no-network, no-volume container."""

    def __init__(
        self,
        postgres_image: str,
        runner: CommandRunner | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.postgres_image = postgres_image
        self.runner = runner or SystemCommandRunner()
        self.timeout_seconds = timeout_seconds

    def verify(self, dump: Path) -> None:
        name = f"ananta-pg-drill-{os.getpid()}-{secrets.token_hex(4)}"
        started = False
        primary_error: BaseException | None = None
        try:
            self.runner.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--pull",
                    "never",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--cap-add",
                    "CHOWN",
                    "--cap-add",
                    "DAC_OVERRIDE",
                    "--cap-add",
                    "FOWNER",
                    "--cap-add",
                    "SETGID",
                    "--cap-add",
                    "SETUID",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,nosuid,noexec,size=2g",
                    "--tmpfs",
                    "/var/run/postgresql:rw,nosuid,noexec,size=16m",
                    "--tmpfs",
                    "/tmp:rw,nosuid,noexec,size=64m",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    self.postgres_image,
                ]
            )
            started = True
            self._wait_ready(name)
            self.runner.run(
                ["docker", "exec", name, "createdb", "--username", "postgres", "restore_drill"]
            )
            self.runner.from_file(
                [
                    "docker",
                    "exec",
                    "--interactive",
                    name,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--username",
                    "postgres",
                    "--dbname",
                    "restore_drill",
                ],
                dump,
            )
            self.runner.capture(
                [
                    "docker",
                    "exec",
                    name,
                    "psql",
                    "--username",
                    "postgres",
                    "--dbname",
                    "restore_drill",
                    "--tuples-only",
                    "--command",
                    "SELECT count(*) FROM pg_catalog.pg_class;",
                ]
            )
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if started:
                try:
                    self.runner.run(["docker", "stop", "--time", "5", name])
                except BackupError as cleanup_error:
                    if primary_error is None:
                        raise
                    primary_error.add_note(
                        "Ephemeral PostgreSQL cleanup also failed: "
                        f"{cleanup_error}"
                    )

    def _wait_ready(self, name: str) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        last_error: BackupError | None = None
        while time.monotonic() < deadline:
            try:
                self.runner.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "pg_isready",
                        "--username",
                        "postgres",
                    ]
                )
                return
            except BackupError as exc:
                last_error = exc
                time.sleep(1)
        raise BackupError(
            f"Ephemeral PostgreSQL restore drill did not become ready: {last_error}"
        )
