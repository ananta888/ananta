from __future__ import annotations

import base64
import re
import sqlite3
import tarfile
from pathlib import Path
from typing import Sequence

import pytest

from scripts.ananta_backup.compose import (
    ComposeAdapter,
    ComposeLayout,
    IsolatedPostgresRestoreDrill,
)
from scripts.ananta_backup.errors import BackupError
from scripts.ananta_backup.filesystem import (
    rename_directory_no_replace,
    require_private_wsl_directory,
)
from scripts.ananta_backup.windows import WindowsDesktopPolicy


class KnownFolderRunner:
    def __init__(self, desktop: str) -> None:
        self.desktop = desktop
        self.commands: list[list[str]] = []

    def capture(self, command: Sequence[str]) -> bytes:
        self.commands.append(list(command))
        return (self.desktop + "\r\n").encode()

    def run(self, command: Sequence[str]) -> None:
        self.commands.append(list(command))


class QuiescenceRunner:
    def __init__(self, active_status: str) -> None:
        self.active_status = active_status
        self.query = ""

    def capture(self, command: Sequence[str]) -> bytes:
        serialized = list(command)
        self.query = serialized[serialized.index("--command") + 1]
        if f"'{self.active_status}'" in self.query:
            return b"1\n"
        return b"0\n"

    def run(self, command: Sequence[str]) -> None:
        raise AssertionError(f"Unexpected command: {list(command)}")


def _quiescence_layout(tmp_path: Path) -> ComposeLayout:
    return ComposeLayout(
        project_name="compose-next",
        runtime_image="ananta:test",
        runtime_user="1000:1000",
        postgres_image="postgres:16",
        postgres_user="ananta",
        postgres_database="ananta",
        named_volumes={},
        ollama_root=tmp_path / "ollama",
        workflow_secret_root=tmp_path / "workflow",
        workspace_root=tmp_path / "workspaces",
        model_profiles_file=tmp_path / "models.yaml",
        model_routing_file=tmp_path / "routing.json",
    )


@pytest.mark.parametrize(
    "active_status",
    [
        "assigned",
        "delegated",
        "in_progress",
        "proposing",
        "running",
        "uncertain",
    ],
)
def test_quiescence_gate_blocks_every_potentially_executing_hub_task_status(
    tmp_path: Path,
    active_status: str,
) -> None:
    compose_file = tmp_path / "compose.yml"
    env_file = tmp_path / ".env"
    compose_file.touch()
    env_file.touch()
    runner = QuiescenceRunner(active_status)
    adapter = ComposeAdapter(
        compose_file,
        env_file,
        runner,  # type: ignore[arg-type]
    )

    with pytest.raises(BackupError, match="potentially executing Hub tasks"):
        adapter.assert_quiescent(_quiescence_layout(tmp_path))

    assert f"'{active_status}'" in runner.query


def test_quiescence_gate_status_set_is_explicit_and_fail_closed() -> None:
    assert set(ComposeAdapter.EXECUTING_TASK_STATUSES) == {
        "assigned",
        "delegated",
        "in_progress",
        "proposing",
        "running",
        "uncertain",
    }


def test_windows_target_matches_redirected_desktop_known_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = tmp_path / "OneDrive" / "Desktop"
    desktop.mkdir(parents=True)
    runner = KnownFolderRunner(r"C:\Users\stuib\OneDrive\Desktop")
    policy = WindowsDesktopPolicy(runner)  # type: ignore[arg-type]
    monkeypatch.setattr(policy, "_to_wsl_path", lambda value: desktop)

    assert policy.validate(desktop) == desktop
    assert runner.commands[0][:3] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
    ]


def test_windows_target_rejects_non_known_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = tmp_path / "Desktop"
    other = tmp_path / "other"
    desktop.mkdir()
    other.mkdir()
    policy = WindowsDesktopPolicy(  # type: ignore[arg-type]
        KnownFolderRunner(r"C:\Users\stuib\OneDrive\Desktop")
    )
    monkeypatch.setattr(policy, "_to_wsl_path", lambda value: desktop)

    with pytest.raises(BackupError, match="redirected Desktop Known Folder"):
        policy.validate(other)


class AtomicMoveRunner:
    def __init__(self) -> None:
        self.run_commands: list[list[str]] = []

    def capture(self, command: Sequence[str]) -> bytes:
        path = Path(command[-1])
        return (
            "C:\\Users\\test\\Desktop\\" + path.name + "\r\n"
        ).encode()

    def run(self, command: Sequence[str]) -> None:
        self.run_commands.append(list(command))


def test_windows_publication_uses_native_atomic_directory_move(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".delivery"
    destination = tmp_path / "ananta-backup-test"
    source.mkdir()
    runner = AtomicMoveRunner()

    WindowsDesktopPolicy(runner).publish_no_replace(  # type: ignore[arg-type]
        source,
        destination,
    )

    command = runner.run_commands[0]
    assert command[:4] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    ]
    assert len(command) == 5
    script = base64.b64decode(command[4], validate=True).decode("utf-16-le")
    assert "[IO.Directory]::Move" in script
    encoded_paths = re.findall(r"FromBase64String\('([^']+)'\)", script)
    assert [
        base64.b64decode(value, validate=True).decode("utf-16-le")
        for value in encoded_paths
    ] == [
        r"C:\Users\test\Desktop\.delivery",
        r"C:\Users\test\Desktop\ananta-backup-test",
    ]


def test_windows_publication_treats_metacharacter_paths_as_encoded_data(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".delivery; ' & $(INJECTION_SENTINEL)"
    destination = tmp_path / "backup; ' & $(SECOND_SENTINEL)"
    source.mkdir()
    runner = AtomicMoveRunner()

    WindowsDesktopPolicy(runner).publish_no_replace(  # type: ignore[arg-type]
        source,
        destination,
    )

    command = runner.run_commands[0]
    assert command[:4] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
    ]
    assert len(command) == 5
    assert "INJECTION_SENTINEL" not in "\0".join(command)
    assert "SECOND_SENTINEL" not in "\0".join(command)

    script = base64.b64decode(command[4], validate=True).decode("utf-16-le")
    assert "INJECTION_SENTINEL" not in script
    assert "SECOND_SENTINEL" not in script
    encoded_paths = re.findall(r"FromBase64String\('([^']+)'\)", script)
    assert [
        base64.b64decode(value, validate=True).decode("utf-16-le")
        for value in encoded_paths
    ] == [
        r"C:\Users\test\Desktop\.delivery; ' & $(INJECTION_SENTINEL)",
        r"C:\Users\test\Desktop\backup; ' & $(SECOND_SENTINEL)",
    ]


def test_windows_publication_refuses_an_existing_destination_before_powershell(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".delivery"
    destination = tmp_path / "ananta-backup-test"
    source.mkdir()
    destination.mkdir()
    runner = AtomicMoveRunner()

    with pytest.raises(BackupError, match="already exists"):
        WindowsDesktopPolicy(runner).publish_no_replace(  # type: ignore[arg-type]
            source,
            destination,
        )

    assert runner.run_commands == []


def test_wsl_target_rejects_windows_interoperability_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount_root = tmp_path / "mnt"
    target = mount_root / "c" / "backup"
    target.mkdir(parents=True)
    monkeypatch.setattr(
        "scripts.ananta_backup.filesystem._WINDOWS_MOUNT_ROOT",
        mount_root,
    )

    with pytest.raises(BackupError, match="private WSL filesystem"):
        require_private_wsl_directory(target, "WSL target")


def test_directory_publication_never_replaces_an_existing_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "new").write_text("new", encoding="utf-8")
    (destination / "existing").write_text("existing", encoding="utf-8")

    with pytest.raises(BackupError, match="already exists"):
        rename_directory_no_replace(source, destination)

    assert (source / "new").read_text(encoding="utf-8") == "new"
    assert (destination / "existing").read_text(encoding="utf-8") == "existing"


def test_worker_sqlite_verification_uses_exported_archive(
    tmp_path: Path,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    database = content / "ananta.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE task (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    archive_path = tmp_path / "alpha-data.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        archive.add(content, arcname=".")

    ComposeAdapter.verify_worker_sqlite_archive(
        archive_path,
        "alpha-data",
    )


class DrillRunner:
    def __init__(self) -> None:
        self.run_commands: list[list[str]] = []
        self.stdin_commands: list[list[str]] = []

    def run(self, command: Sequence[str]) -> None:
        self.run_commands.append(list(command))

    def from_file(self, command: Sequence[str], input_path: Path) -> bytes:
        self.stdin_commands.append(list(command))
        return b""

    def capture(self, command: Sequence[str]) -> bytes:
        return b"1\n"


def test_postgres_restore_drill_uses_no_live_volume_network_or_port(
    tmp_path: Path,
) -> None:
    dump = tmp_path / "ananta.dump"
    dump.write_bytes(b"PGDMP-test")
    runner = DrillRunner()

    IsolatedPostgresRestoreDrill(
        "postgres:16@test-digest", runner  # type: ignore[arg-type]
    ).verify(dump)

    start = runner.run_commands[0]
    assert start[:3] == ["docker", "run", "--detach"]
    assert ["--network", "none"] == start[start.index("--network") :][:2]
    assert "--publish" not in start
    assert "--volume" not in start
    assert start.count("--tmpfs") == 3
    assert ["--pull", "never"] == start[start.index("--pull") :][:2]
    assert runner.stdin_commands
    assert "pg_restore" in runner.stdin_commands[0]
    assert runner.run_commands[-1][:2] == ["docker", "stop"]
