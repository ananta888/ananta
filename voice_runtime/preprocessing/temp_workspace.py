from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..audio import sanitize_audio_filename


@dataclass(frozen=True)
class AudioWorkspace:
    root: Path

    def write_bytes(self, filename: str, payload: bytes, *, max_bytes: int) -> Path:
        if len(payload) > max_bytes:
            raise ValueError("audio workspace payload exceeds configured limit")
        safe_name = sanitize_audio_filename(filename, fallback="audio.bin")
        destination = self.root / safe_name
        if destination.parent != self.root:
            raise ValueError("audio workspace path escapes its root")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def path_for(self, filename: str) -> Path:
        safe_name = sanitize_audio_filename(filename, fallback="artifact.bin")
        path = self.root / safe_name
        if path.parent != self.root:
            raise ValueError("audio workspace path escapes its root")
        return path

    def read_bounded_bytes(self, filename: str, *, max_bytes: int) -> bytes | None:
        """Read a regular child-produced artifact without an unbounded allocation."""

        if max_bytes <= 0:
            raise ValueError("audio workspace read limit must be positive")
        source = self.path_for(filename)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError("audio workspace artifact is not safely readable") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("audio workspace artifact is not a regular file")
            if metadata.st_size > max_bytes:
                raise ValueError("audio workspace artifact exceeds configured limit")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                payload = handle.read(max_bytes + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > max_bytes:
            raise ValueError("audio workspace artifact exceeds configured limit")
        return payload


@contextmanager
def temporary_audio_workspace(*, prefix: str = "ananta-voice-") -> Iterator[AudioWorkspace]:
    with tempfile.TemporaryDirectory(prefix=prefix) as directory:
        root = Path(directory)
        root.chmod(0o700)
        yield AudioWorkspace(root=root)
