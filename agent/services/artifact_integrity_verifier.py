"""Bounded integrity verification for immutable Hub-side artifacts."""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol


@dataclass(frozen=True)
class VerifiedArtifact:
    """Filesystem identity proven to contain one expected SHA-256 payload."""

    path: Path
    device: int
    inode: int
    mtime_ns: int
    size_bytes: int
    sha256: str


class ArtifactIntegrityVerifierPort(Protocol):
    def verify(
        self,
        *,
        path: Path,
        expected_sha256: str,
        maximum_bytes: int,
        checkpoint: Callable[[], object] | None = None,
    ) -> VerifiedArtifact: ...


class BoundedArtifactIntegrityVerifier:
    """Hash files once per stable inode signature and expected digest.

    The cache is deliberately small and process-local. A changed inode,
    mtime, size, path, or expected digest always forces a fresh hash. The
    before/after signature check also rejects files changed while hashing.
    """

    _READ_BYTES = 1024 * 1024

    def __init__(self, *, maximum_entries: int = 16) -> None:
        if maximum_entries < 1:
            raise ValueError("artifact_integrity_cache_size_invalid")
        self._maximum_entries = int(maximum_entries)
        self._cache: OrderedDict[
            tuple[str, int, int, int, int, str],
            VerifiedArtifact,
        ] = OrderedDict()
        self._lock = RLock()

    def verify(
        self,
        *,
        path: Path,
        expected_sha256: str,
        maximum_bytes: int,
        checkpoint: Callable[[], object] | None = None,
    ) -> VerifiedArtifact:
        self._checkpoint(checkpoint)
        digest = str(expected_sha256 or "").lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("artifact_integrity_digest_invalid")
        if maximum_bytes < 1:
            raise ValueError("artifact_integrity_size_limit_invalid")
        if path.is_symlink() or not path.is_file():
            raise ValueError("artifact_integrity_file_invalid")
        before = path.stat()
        if before.st_size < 0 or before.st_size > maximum_bytes:
            raise ValueError("artifact_integrity_size_invalid")
        key = self._key(path=path, stat=before, digest=digest)
        with self._lock:
            cached = self._cache.pop(key, None)
            if cached is not None:
                self._cache[key] = cached
                self._checkpoint(checkpoint)
                return cached

        actual = self._sha256(path, checkpoint=checkpoint)
        after = path.stat()
        if self._signature(before) != self._signature(after):
            raise ValueError("artifact_integrity_changed_during_verification")
        if not _constant_time_equal(actual, digest):
            raise ValueError("artifact_integrity_hash_drift")
        verified = VerifiedArtifact(
            path=path,
            device=int(after.st_dev),
            inode=int(after.st_ino),
            mtime_ns=int(after.st_mtime_ns),
            size_bytes=int(after.st_size),
            sha256=digest,
        )
        with self._lock:
            self._cache[key] = verified
            while len(self._cache) > self._maximum_entries:
                self._cache.popitem(last=False)
        return verified

    @classmethod
    def _sha256(
        cls,
        path: Path,
        *,
        checkpoint: Callable[[], object] | None,
    ) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(cls._READ_BYTES), b""):
                cls._checkpoint(checkpoint)
                digest.update(chunk)
        cls._checkpoint(checkpoint)
        return digest.hexdigest()

    @staticmethod
    def _checkpoint(callback: Callable[[], object] | None) -> None:
        if callback is not None:
            callback()

    @staticmethod
    def _signature(stat: os.stat_result) -> tuple[int, int, int, int]:
        return (
            int(stat.st_dev),
            int(stat.st_ino),
            int(stat.st_mtime_ns),
            int(stat.st_size),
        )

    @classmethod
    def _key(
        cls,
        *,
        path: Path,
        stat: os.stat_result,
        digest: str,
    ) -> tuple[str, int, int, int, int, str]:
        device, inode, mtime_ns, size_bytes = cls._signature(stat)
        return (
            str(path),
            device,
            inode,
            mtime_ns,
            size_bytes,
            digest,
        )


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


artifact_integrity_verifier = BoundedArtifactIntegrityVerifier()


def get_artifact_integrity_verifier() -> BoundedArtifactIntegrityVerifier:
    return artifact_integrity_verifier


__all__ = [
    "ArtifactIntegrityVerifierPort",
    "BoundedArtifactIntegrityVerifier",
    "VerifiedArtifact",
    "get_artifact_integrity_verifier",
]
