"""Durable, worker-local replay protection for vector-index dispatches."""

from __future__ import annotations

import math
import os
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

from ananta_contracts.vector_index_dispatch import (
    canonicalize_vector_index_worker_audience,
)

_DEFAULT_LEDGER_PATH = (
    "/app/data/vector-index-replay/vector-index-task-replay.sqlite3"
)
_DEFAULT_RECEIPT_RETENTION_SECONDS = 86_400.0
_MIN_RECEIPT_RETENTION_SECONDS = 3_600.0
_MAX_RECEIPT_RETENTION_SECONDS = 31_536_000.0
_MAX_SEQUENCE = 2**63 - 1
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_DISPATCH_PHASES = frozenset({"propose", "execute"})


@dataclass(frozen=True, slots=True)
class _DispatchReceipt:
    job_id: str
    attempt_id: str
    sequence: int
    phase: str
    audience: str
    expires_at: float


def _dispatch_receipt(
    *,
    job_id: str,
    attempt_id: str,
    sequence: int,
    phase: str,
    audience: str,
    expires_at: float,
) -> _DispatchReceipt:
    normalized_job_id = str(job_id or "").strip()
    normalized_attempt_id = str(attempt_id or "").strip()
    normalized_phase = str(phase or "").strip().lower()
    if (
        not normalized_job_id
        or len(normalized_job_id.encode("utf-8")) > 256
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in normalized_job_id
        )
        or _ATTEMPT_ID.fullmatch(normalized_attempt_id) is None
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or not 1 <= sequence <= _MAX_SEQUENCE
        or normalized_phase not in _DISPATCH_PHASES
    ):
        raise ValueError("vector_index_replay_receipt_invalid")
    try:
        normalized_expiry = float(expires_at)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "vector_index_replay_receipt_invalid"
        ) from exc
    if (
        not math.isfinite(normalized_expiry)
        or normalized_expiry <= 0
    ):
        raise ValueError("vector_index_replay_receipt_invalid")
    return _DispatchReceipt(
        job_id=normalized_job_id,
        attempt_id=normalized_attempt_id,
        sequence=sequence,
        phase=normalized_phase,
        audience=canonicalize_vector_index_worker_audience(
            audience
        ),
        expires_at=normalized_expiry,
    )


class VectorIndexReplayGuard(Protocol):
    """Atomically consume one Hub-issued dispatch attempt."""

    def consume(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        audience: str,
        expires_at: float,
    ) -> None: ...


class InMemoryVectorIndexReplayGuard:
    """Process-local implementation intended for isolated unit tests."""

    def __init__(self) -> None:
        self._consumed: set[tuple[str, str]] = set()
        self._high_watermarks: dict[str, _DispatchReceipt] = {}
        self._lock = threading.Lock()

    def consume(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        audience: str,
        expires_at: float,
    ) -> None:
        receipt = _dispatch_receipt(
            job_id=job_id,
            attempt_id=attempt_id,
            sequence=sequence,
            phase=phase,
            audience=audience,
            expires_at=expires_at,
        )
        key = (receipt.job_id, receipt.attempt_id)
        with self._lock:
            high_watermark = self._high_watermarks.get(
                receipt.job_id
            )
            if (
                key in self._consumed
                or (
                    high_watermark is not None
                    and receipt.sequence
                    <= high_watermark.sequence
                )
            ):
                raise ValueError("vector_index_task_replay_detected")
            self._consumed.add(key)
            self._high_watermarks[receipt.job_id] = receipt


class SqliteVectorIndexReplayGuard:
    """Persist replay receipts across requests and Worker restarts.

    Each Worker owns its ledger. Cross-Worker replay is prevented separately
    by the signed Worker audience, so no Worker-to-Worker coordination or
    shared control-plane storage is introduced.

    Python's sqlite API accepts paths rather than an already verified file
    descriptor. The implementation therefore walks every configured path
    component with ``dir_fd`` plus ``O_NOFOLLOW``, requires a private direct
    parent, rejects non-sticky writable ancestors and anchors each sqlite open
    through the verified parent descriptor. It revalidates the same directory
    and regular-file identities around every transaction. A process already
    compromised under the Worker's own UID can still race sqlite's internal
    path operations; that same process already has the Worker's execution
    authority.

    Expired exact-attempt receipts have a bounded retention window. Per-job
    sequence high-watermarks are permanent compact tombstones: deleting them
    would make an old signed sequence replayable after a restart or clock
    rollback.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        receipt_retention_seconds: float = (
            _DEFAULT_RECEIPT_RETENTION_SECONDS
        ),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        if (
            not self._path.is_absolute()
            or not self._path.name
        ):
            raise RuntimeError(
                "vector_index_replay_ledger_path_invalid"
            )
        if self._path.resolve(strict=False) != self._path:
            raise RuntimeError(
                "vector_index_replay_ledger_unsafe"
            )
        try:
            retention = float(receipt_retention_seconds)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "vector_index_replay_retention_invalid"
            ) from exc
        if (
            not math.isfinite(retention)
            or not _MIN_RECEIPT_RETENTION_SECONDS
            <= retention
            <= _MAX_RECEIPT_RETENTION_SECONDS
        ):
            raise RuntimeError(
                "vector_index_replay_retention_invalid"
            )
        self._receipt_retention_seconds = retention
        self._clock = clock
        self._parent_identity: tuple[int, int] | None = None
        self._identity: tuple[int, int] | None = None
        self._initialize()

    def _initialize(self) -> None:
        parent_descriptor = self._open_parent_chain(create=True)
        try:
            parent_metadata = os.fstat(parent_descriptor)
            self._parent_identity = (
                int(parent_metadata.st_dev),
                int(parent_metadata.st_ino),
            )
        finally:
            os.close(parent_descriptor)
        descriptor = self._open_verified(create=True)
        try:
            metadata = os.fstat(descriptor)
            self._validate_file_metadata(metadata)
            self._identity = (
                int(metadata.st_dev),
                int(metadata.st_ino),
            )
            self._assert_path_identity()
        finally:
            os.close(descriptor)
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS consumed_dispatches (
                        job_id TEXT NOT NULL,
                        attempt_id TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        PRIMARY KEY (job_id, attempt_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS consumed_dispatches_v2 (
                        job_id TEXT NOT NULL,
                        attempt_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        phase TEXT NOT NULL,
                        audience TEXT NOT NULL,
                        expires_at REAL NOT NULL,
                        PRIMARY KEY (job_id, attempt_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS dispatch_high_watermarks (
                        job_id TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        phase TEXT NOT NULL,
                        audience TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                        ix_consumed_dispatches_v2_expiry
                    ON consumed_dispatches_v2 (expires_at)
                    """
                )
                self._assert_path_identity()
        except sqlite3.Error as exc:
            raise RuntimeError("vector_index_replay_ledger_unavailable") from exc

    @staticmethod
    def _directory_open_flags() -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if (
            not isinstance(no_follow, int)
            or no_follow == 0
            or not isinstance(directory, int)
            or directory == 0
        ):
            raise RuntimeError("vector_index_replay_ledger_unsafe")
        return (
            os.O_RDONLY
            | no_follow
            | directory
            | int(getattr(os, "O_CLOEXEC", 0))
        )

    @staticmethod
    def _validate_directory_metadata(
        metadata: os.stat_result,
        *,
        direct_parent: bool,
    ) -> None:
        effective_uid = getattr(os, "geteuid", lambda: -1)()
        mode = metadata.st_mode
        if (
            not stat.S_ISDIR(mode)
            or metadata.st_uid not in {0, effective_uid}
        ):
            raise RuntimeError("vector_index_replay_ledger_unsafe")
        if direct_parent:
            if (
                mode & (stat.S_IRWXG | stat.S_IRWXO)
                or mode & stat.S_IRWXU != stat.S_IRWXU
            ):
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            return
        if (
            mode & (stat.S_IWGRP | stat.S_IWOTH)
            and not mode & stat.S_ISVTX
        ):
            raise RuntimeError("vector_index_replay_ledger_unsafe")

    def _open_parent_chain(self, *, create: bool) -> int:
        flags = self._directory_open_flags()
        try:
            descriptor = os.open("/", flags)
        except OSError as exc:
            raise RuntimeError(
                "vector_index_replay_ledger_unavailable"
            ) from exc
        parts = self._path.parent.parts[1:]
        try:
            if not parts:
                self._validate_directory_metadata(
                    os.fstat(descriptor),
                    direct_parent=True,
                )
            for index, component in enumerate(parts):
                direct_parent = index == len(parts) - 1
                self._validate_directory_metadata(
                    os.fstat(descriptor),
                    direct_parent=False,
                )
                try:
                    child = os.open(
                        component,
                        flags,
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise RuntimeError(
                            "vector_index_replay_ledger_unavailable"
                        ) from None
                    try:
                        os.mkdir(
                            component,
                            0o700,
                            dir_fd=descriptor,
                        )
                    except FileExistsError:
                        pass
                    child = os.open(
                        component,
                        flags,
                        dir_fd=descriptor,
                    )
                os.close(descriptor)
                descriptor = child
                self._validate_directory_metadata(
                    os.fstat(descriptor),
                    direct_parent=direct_parent,
                )
            path_metadata = os.stat(
                self._path.parent,
                follow_symlinks=False,
            )
            descriptor_metadata = os.fstat(descriptor)
            if (
                int(path_metadata.st_dev),
                int(path_metadata.st_ino),
            ) != (
                int(descriptor_metadata.st_dev),
                int(descriptor_metadata.st_ino),
            ):
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            return descriptor
        except RuntimeError:
            os.close(descriptor)
            raise
        except OSError as exc:
            os.close(descriptor)
            raise RuntimeError(
                "vector_index_replay_ledger_unsafe"
            ) from exc

    def _open_parent_verified(self) -> int:
        descriptor = self._open_parent_chain(create=False)
        try:
            metadata = os.fstat(descriptor)
            identity = (
                int(metadata.st_dev),
                int(metadata.st_ino),
            )
            if (
                self._parent_identity is None
                or identity != self._parent_identity
            ):
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _validate_file_metadata(metadata: os.stat_result) -> None:
        effective_uid = getattr(os, "geteuid", lambda: -1)()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid not in {0, effective_uid}
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or not metadata.st_mode & stat.S_IRUSR
            or not metadata.st_mode & stat.S_IWUSR
        ):
            raise RuntimeError("vector_index_replay_ledger_unsafe")

    def _open_verified(
        self,
        *,
        create: bool,
        parent_descriptor: int | None = None,
    ) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if not isinstance(no_follow, int) or no_follow == 0:
            raise RuntimeError("vector_index_replay_ledger_unsafe")
        flags = os.O_RDWR | no_follow
        flags |= int(getattr(os, "O_CLOEXEC", 0))
        if create:
            flags |= os.O_CREAT
        owns_parent_descriptor = parent_descriptor is None
        parent = (
            self._open_parent_verified()
            if parent_descriptor is None
            else parent_descriptor
        )
        try:
            descriptor = os.open(
                self._path.name,
                flags,
                0o600,
                dir_fd=parent,
            )
        except OSError as exc:
            if owns_parent_descriptor:
                os.close(parent)
            raise RuntimeError(
                "vector_index_replay_ledger_unavailable"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if create:
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid
                    not in {
                        0,
                        getattr(os, "geteuid", lambda: -1)(),
                    }
                ):
                    raise RuntimeError(
                        "vector_index_replay_ledger_unsafe"
                    )
                os.fchmod(descriptor, 0o600)
                metadata = os.fstat(descriptor)
            self._validate_file_metadata(metadata)
            identity = (
                int(metadata.st_dev),
                int(metadata.st_ino),
            )
            if (
                self._identity is not None
                and identity != self._identity
            ):
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            path_metadata = os.stat(
                self._path.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            if (
                int(path_metadata.st_dev),
                int(path_metadata.st_ino),
            ) != identity:
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise
        finally:
            if owns_parent_descriptor:
                os.close(parent)

    def _assert_path_identity(
        self,
        *,
        parent_descriptor: int | None = None,
    ) -> None:
        if self._identity is None:
            raise RuntimeError("vector_index_replay_ledger_unsafe")
        descriptor = self._open_verified(
            create=False,
            parent_descriptor=parent_descriptor,
        )
        os.close(descriptor)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        parent_descriptor = self._open_parent_verified()
        self._assert_path_identity(
            parent_descriptor=parent_descriptor,
        )
        connection: sqlite3.Connection | None = None
        try:
            descriptor_path = (
                Path("/proc/self/fd")
                / str(parent_descriptor)
                / self._path.name
            )
            if not Path("/proc/self/fd").is_dir():
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            connection = sqlite3.connect(
                f"{descriptor_path.as_uri()}?mode=rw",
                timeout=5.0,
                isolation_level=None,
                uri=True,
            )
            self._assert_path_identity(
                parent_descriptor=parent_descriptor,
            )
            database_path = str(
                connection.execute(
                    "PRAGMA database_list"
                ).fetchone()[2]
                or ""
            )
            if (
                not database_path
                or Path(database_path).resolve(strict=True)
                != self._path
            ):
                raise RuntimeError(
                    "vector_index_replay_ledger_unsafe"
                )
            connection.execute("PRAGMA trusted_schema=OFF")
            yield connection
        finally:
            if connection is not None:
                connection.close()
            os.close(parent_descriptor)

    def consume(
        self,
        *,
        job_id: str,
        attempt_id: str,
        sequence: int,
        phase: str,
        audience: str,
        expires_at: float,
    ) -> None:
        receipt = _dispatch_receipt(
            job_id=job_id,
            attempt_id=attempt_id,
            sequence=sequence,
            phase=phase,
            audience=audience,
            expires_at=expires_at,
        )
        try:
            now = float(self._clock())
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "vector_index_replay_clock_invalid"
            ) from exc
        if not math.isfinite(now) or now < 0:
            raise RuntimeError(
                "vector_index_replay_clock_invalid"
            )
        receipt_cutoff = (
            now - self._receipt_retention_seconds
        )
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    DELETE FROM consumed_dispatches_v2
                    WHERE expires_at < ?
                    """,
                    (receipt_cutoff,),
                )
                legacy_receipt = connection.execute(
                    """
                    SELECT 1
                    FROM consumed_dispatches
                    WHERE job_id = ? AND attempt_id = ?
                    """,
                    (receipt.job_id, receipt.attempt_id),
                ).fetchone()
                current = connection.execute(
                    """
                    SELECT attempt_id, sequence
                    FROM dispatch_high_watermarks
                    WHERE job_id = ?
                    """,
                    (receipt.job_id,),
                ).fetchone()
                repeated_attempt = connection.execute(
                    """
                    SELECT 1
                    FROM consumed_dispatches_v2
                    WHERE job_id = ? AND attempt_id = ?
                    """,
                    (receipt.job_id, receipt.attempt_id),
                ).fetchone()
                if (
                    legacy_receipt is not None
                    or repeated_attempt is not None
                    or (
                        current is not None
                        and (
                            receipt.attempt_id
                            == str(current[0])
                            or receipt.sequence
                            <= int(current[1])
                        )
                    )
                ):
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "vector_index_task_replay_detected"
                    )
                try:
                    connection.execute(
                        """
                        INSERT INTO consumed_dispatches_v2 (
                            job_id,
                            attempt_id,
                            sequence,
                            phase,
                            audience,
                            expires_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            receipt.job_id,
                            receipt.attempt_id,
                            receipt.sequence,
                            receipt.phase,
                            receipt.audience,
                            receipt.expires_at,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO dispatch_high_watermarks (
                            job_id,
                            attempt_id,
                            sequence,
                            phase,
                            audience,
                            expires_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(job_id) DO UPDATE SET
                            attempt_id = excluded.attempt_id,
                            sequence = excluded.sequence,
                            phase = excluded.phase,
                            audience = excluded.audience,
                            expires_at = excluded.expires_at
                        WHERE excluded.sequence >
                            dispatch_high_watermarks.sequence
                        """,
                        (
                            receipt.job_id,
                            receipt.attempt_id,
                            receipt.sequence,
                            receipt.phase,
                            receipt.audience,
                            receipt.expires_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "vector_index_task_replay_detected"
                    ) from exc
                self._assert_path_identity()
                connection.execute("COMMIT")
                self._assert_path_identity()
        except ValueError:
            raise
        except RuntimeError:
            raise
        except sqlite3.Error as exc:
            raise RuntimeError(
                "vector_index_replay_ledger_unavailable"
            ) from exc


def build_vector_index_replay_guard(
    source: Mapping[str, str] | None = None,
) -> SqliteVectorIndexReplayGuard:
    """Build the production replay guard from the Worker environment."""

    values = os.environ if source is None else source
    path = str(
        values.get("ANANTA_VECTOR_INDEX_TASK_REPLAY_LEDGER_FILE")
        or _DEFAULT_LEDGER_PATH
    ).strip()
    if (
        not path
        or "\x00" in path
        or not Path(path).is_absolute()
    ):
        raise RuntimeError("vector_index_replay_ledger_path_invalid")
    retention_value = str(
        values.get(
            "ANANTA_VECTOR_INDEX_TASK_REPLAY_RECEIPT_RETENTION_SECONDS"
        )
        or _DEFAULT_RECEIPT_RETENTION_SECONDS
    ).strip()
    try:
        retention_seconds = float(retention_value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "vector_index_replay_retention_invalid"
        ) from exc
    return SqliteVectorIndexReplayGuard(
        path,
        receipt_retention_seconds=retention_seconds,
    )


__all__ = [
    "InMemoryVectorIndexReplayGuard",
    "SqliteVectorIndexReplayGuard",
    "VectorIndexReplayGuard",
    "build_vector_index_replay_guard",
    "canonicalize_vector_index_worker_audience",
]
