"""Windows Known Folder policy for the second encrypted copy."""

from __future__ import annotations

import base64
import os
import re
import stat
from pathlib import Path

from .errors import BackupError
from .process import CommandRunner, SystemCommandRunner

_WINDOWS_PATH = re.compile(r"^([A-Za-z]):\\(.+)$")


class WindowsDesktopPolicy:
    """Allow only the Desktop path Windows currently reports to WSL."""

    _ATOMIC_MOVE_SCRIPT = (
        '$ErrorActionPreference="Stop"; '
        "$Source=[Text.Encoding]::Unicode.GetString("
        "[Convert]::FromBase64String('{source}'));"
        "$Destination=[Text.Encoding]::Unicode.GetString("
        "[Convert]::FromBase64String('{destination}'));"
        "[IO.Directory]::Move($Source,$Destination)"
    )

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or SystemCommandRunner()

    def validate(self, target: Path) -> Path:
        script = (
            "$OutputEncoding=[Console]::OutputEncoding="
            "[System.Text.UTF8Encoding]::new();"
            "[Environment]::GetFolderPath("
            "[Environment+SpecialFolder]::DesktopDirectory)"
        )
        output = self.runner.capture(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        try:
            windows_path = output.decode("utf-8", errors="strict").strip().strip("\ufeff")
        except UnicodeError as exc:
            raise BackupError("Windows Desktop Known Folder was not valid UTF-8") from exc
        expected = self._to_wsl_path(windows_path).resolve(strict=True)
        actual = target.resolve(strict=True)
        if actual != expected:
            raise BackupError(
                "Windows target must equal the redirected Desktop Known Folder "
                f"reported by Windows: {expected}"
            )
        return actual

    def publish_no_replace(
        self,
        source: Path,
        destination: Path,
    ) -> None:
        """Atomically publish on DrvFS without replacing an existing folder."""

        try:
            metadata = source.lstat()
            source_parent = source.parent.resolve(strict=True)
            destination_parent = destination.parent.resolve(strict=True)
        except OSError as exc:
            raise BackupError(
                f"Cannot inspect Windows publication paths: {exc}"
            ) from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or source_parent != destination_parent
        ):
            raise BackupError(
                "Windows publication requires a real staging directory "
                "beside its destination"
            )
        if os.path.lexists(destination):
            raise BackupError(
                f"Backup package already exists: {destination.name}"
            )
        source_windows = self._wsl_to_windows_argument(source)
        destination_windows = self._wsl_to_windows_argument(destination)
        encoded_command = self._encoded_atomic_move_command(
            source_windows,
            destination_windows,
        )
        try:
            self.runner.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded_command,
                ]
            )
        except BackupError as exc:
            if os.path.lexists(destination):
                raise BackupError(
                    f"Backup package already exists: {destination.name}"
                ) from exc
            raise BackupError(
                f"Windows atomic no-clobber publication failed: {exc}"
            ) from exc

    @classmethod
    def _encoded_atomic_move_command(
        cls,
        source: str,
        destination: str,
    ) -> str:
        script = cls._ATOMIC_MOVE_SCRIPT.format(
            source=cls._encode_powershell_data(source),
            destination=cls._encode_powershell_data(destination),
        )
        return cls._encode_powershell_data(script)

    @staticmethod
    def _encode_powershell_data(value: str) -> str:
        """Encode data without exposing it to the PowerShell command parser."""

        return base64.b64encode(value.encode("utf-16-le")).decode("ascii")

    def _wsl_to_windows_argument(self, path: Path) -> str:
        output = self.runner.capture(
            ["wslpath", "-w", str(path)]
        )
        try:
            windows_path = output.decode(
                "utf-8",
                errors="strict",
            ).strip().strip("\ufeff")
        except UnicodeError as exc:
            raise BackupError(
                "wslpath returned an invalid Windows path"
            ) from exc
        if (
            _WINDOWS_PATH.fullmatch(windows_path) is None
            or "\x00" in windows_path
            or "\n" in windows_path
            or "\r" in windows_path
        ):
            raise BackupError(
                "wslpath returned an unsupported Windows path"
            )
        return windows_path

    @staticmethod
    def _to_wsl_path(windows_path: str) -> Path:
        match = _WINDOWS_PATH.fullmatch(windows_path)
        if match is None:
            raise BackupError("Windows returned an unsupported Desktop Known Folder path")
        drive, tail = match.groups()
        parts = tail.split("\\")
        if any(part in {"", ".", ".."} for part in parts):
            raise BackupError("Windows Desktop Known Folder path is unsafe")
        return Path("/mnt") / drive.lower() / Path(*parts)
