"""Stable tree capture, streaming compression, and OpenPGP encryption."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Sequence

from .errors import BackupError

_FINGERPRINT = re.compile(r"^(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})$")
_MAX_ARCHIVE_ENTRIES = 1_000_000
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024 * 1024
_MAX_ARCHIVE_FILE_BYTES = 128 * 1024 * 1024 * 1024
_MAX_TAR_METADATA_BYTES = 1024 * 1024
_MAX_TAR_EXTENSION_DEPTH = 32


class BoundedTarInfo(tarfile.TarInfo):
    """Reject oversized or recursive tar metadata before allocation."""

    def _proc_pax(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        self._enter_extension(archive)
        try:
            payload = self._peek_extension_payload(archive)
            if any(
                key.startswith(b"GNU.sparse.")
                for key in self._parse_pax_keys(payload)
            ):
                raise tarfile.InvalidHeaderError(
                    "GNU sparse PAX metadata is forbidden"
                )
            return super()._proc_pax(archive)
        finally:
            self._leave_extension(archive)

    def _proc_gnulong(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        self._enter_extension(archive)
        try:
            self._require_bounded_extension()
            return super()._proc_gnulong(archive)
        finally:
            self._leave_extension(archive)

    def _proc_sparse(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        raise tarfile.InvalidHeaderError("GNU sparse entries are forbidden")

    def _require_bounded_extension(self) -> None:
        if self.size < 0 or self.size > _MAX_TAR_METADATA_BYTES:
            raise tarfile.InvalidHeaderError(
                "tar extended metadata exceeds the safety limit"
            )

    @staticmethod
    def _enter_extension(archive: tarfile.TarFile) -> None:
        depth = getattr(archive, "_ananta_extension_depth", 0)
        if (
            type(depth) is not int
            or depth < 0
            or depth >= _MAX_TAR_EXTENSION_DEPTH
        ):
            raise tarfile.InvalidHeaderError(
                "tar extension header nesting exceeds the safety limit"
            )
        archive._ananta_extension_depth = depth + 1

    @staticmethod
    def _leave_extension(archive: tarfile.TarFile) -> None:
        depth = getattr(archive, "_ananta_extension_depth", 0)
        archive._ananta_extension_depth = max(0, depth - 1)

    @staticmethod
    def _parse_pax_keys(payload: bytes) -> tuple[bytes, ...]:
        """Parse bounded PAX framing without interpreting attacker values."""

        position = 0
        keys: list[bytes] = []
        while position < len(payload) and payload[position] != 0:
            separator = payload.find(b" ", position)
            length_field = payload[position:separator]
            if (
                separator <= position
                or len(length_field) > 20
                or not length_field.isdigit()
            ):
                raise tarfile.InvalidHeaderError("invalid PAX metadata")
            try:
                record_length = int(length_field)
            except ValueError as exc:
                raise tarfile.InvalidHeaderError(
                    "invalid PAX metadata"
                ) from exc
            record_end = position + record_length
            minimum_length = separator - position + 4
            if (
                record_length < minimum_length
                or record_end > len(payload)
                or payload[record_end - 1] != 0x0A
            ):
                raise tarfile.InvalidHeaderError("invalid PAX metadata")
            assignment = payload[separator + 1 : record_end - 1]
            key, equals, _value = assignment.partition(b"=")
            if not key or equals != b"=":
                raise tarfile.InvalidHeaderError("invalid PAX metadata")
            keys.append(key)
            position = record_end
        return tuple(keys)

    def _peek_extension_payload(
        self,
        archive: tarfile.TarFile,
    ) -> bytes:
        self._require_bounded_extension()
        stream = archive.fileobj
        position = stream.tell()
        block_size = self._block(self.size)
        payload = stream.read(block_size)
        if len(payload) != block_size:
            raise tarfile.InvalidHeaderError(
                "tar extended metadata is truncated"
            )
        try:
            stream.seek(position)
        except (OSError, tarfile.StreamError):
            # tarfile's r| adapter cannot seek backwards.  Restore feeds it an
            # already-decompressed raw tar stream, so its bounded buffer can
            # replay this metadata without touching the underlying pipe.
            if (
                stream.__class__.__name__ != "_Stream"
                or getattr(stream, "comptype", None) != "tar"
                or not isinstance(getattr(stream, "buf", None), bytes)
                or getattr(stream, "pos", None) != position + block_size
            ):
                raise tarfile.InvalidHeaderError(
                    "tar metadata replay is unavailable"
                )
            stream.buf = payload + stream.buf
            stream.pos = position
        return payload[: self.size]


@dataclass
class _TarStreamValidator:
    """Apply one reusable resource and path policy to a tar member stream."""

    max_entries: int = _MAX_ARCHIVE_ENTRIES
    max_total_bytes: int = _MAX_ARCHIVE_BYTES
    max_file_bytes: int = _MAX_ARCHIVE_FILE_BYTES
    entry_count: int = 0
    total_bytes: int = 0

    def validate(self, member: tarfile.TarInfo) -> PurePosixPath | None:
        if member.name in {".", "./"} and member.isdir():
            return None
        self.entry_count += 1
        if self.entry_count > self.max_entries:
            raise BackupError("Archive contains too many entries")
        if member.size < 0 or member.size > self.max_file_bytes:
            raise BackupError(f"Archive entry is too large: {member.name}")
        if member.isreg():
            self.total_bytes += member.size
            if self.total_bytes > self.max_total_bytes:
                raise BackupError("Archive exceeds the restore size limit")
        if not (
            member.isdir()
            or member.isreg()
            or member.issym()
            or member.islnk()
        ):
            raise BackupError(
                f"Special archive entry is forbidden: {member.name}"
            )
        return SafeTarExtractor.safe_relative(member.name)


@dataclass(frozen=True)
class TreeEntry:
    """One lstat snapshot used to detect concurrent source changes."""

    relative: PurePosixPath
    mode: int
    size: int
    mtime_ns: int
    device: int
    inode: int
    link_target: str | None

    @property
    def kind(self) -> str:
        if stat.S_ISDIR(self.mode):
            return "directory"
        if stat.S_ISREG(self.mode):
            return "file"
        if stat.S_ISLNK(self.mode):
            return "symlink"
        return "special"


@dataclass(frozen=True)
class TreePlan:
    """Immutable inventory of one directory tree."""

    label: str
    source: Path
    archive_root: PurePosixPath | None
    root_entry: TreeEntry
    entries: tuple[TreeEntry, ...]
    strict_files: bool = False

    @classmethod
    def capture(
        cls,
        label: str,
        source: Path,
        archive_root: str | None,
        *,
        strict_files: bool = False,
    ) -> "TreePlan":
        try:
            root_metadata = source.lstat()
        except OSError as exc:
            raise BackupError(f"Cannot inspect {label}: {exc}") from exc
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise BackupError(f"{label} must be a real directory: {source}")
        resolved = source.resolve(strict=True)
        root_metadata = resolved.lstat()
        root_entry = TreeEntry(
            relative=PurePosixPath(),
            mode=root_metadata.st_mode,
            size=root_metadata.st_size,
            mtime_ns=root_metadata.st_mtime_ns,
            device=root_metadata.st_dev,
            inode=root_metadata.st_ino,
            link_target=None,
        )
        entries: list[TreeEntry] = []

        def visit(directory: Path, relative_parent: PurePosixPath) -> None:
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise BackupError(f"Cannot read {label}: {directory}: {exc}") from exc
            for child in children:
                relative = relative_parent / child.name
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise BackupError(f"Cannot inspect {label}: {child.path}: {exc}") from exc
                link_target: str | None = None
                if stat.S_ISLNK(metadata.st_mode):
                    if strict_files:
                        raise BackupError(f"{label} must not contain symlinks: {relative}")
                    link_target = os.readlink(child.path)
                    cls._validate_source_symlink(resolved, Path(child.path), link_target)
                elif not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    raise BackupError(f"{label} contains a special file: {relative}")
                if strict_files and stat.S_ISREG(metadata.st_mode):
                    if metadata.st_nlink != 1:
                        raise BackupError(f"{label} contains a hard-linked file: {relative}")
                    cls._verify_readable_file(Path(child.path), label)
                entries.append(
                    TreeEntry(
                        relative=relative,
                        mode=metadata.st_mode,
                        size=metadata.st_size,
                        mtime_ns=metadata.st_mtime_ns,
                        device=metadata.st_dev,
                        inode=metadata.st_ino,
                        link_target=link_target,
                    )
                )
                if stat.S_ISDIR(metadata.st_mode):
                    visit(Path(child.path), relative)

        visit(resolved, PurePosixPath())
        root = PurePosixPath(archive_root) if archive_root else None
        if root and (root.is_absolute() or ".." in root.parts):
            raise BackupError(f"Unsafe archive root for {label}")
        return cls(
            label,
            resolved,
            root,
            root_entry,
            tuple(entries),
            strict_files,
        )

    @staticmethod
    def _verify_readable_file(path: Path, label: str) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                os.read(descriptor, 1)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise BackupError(f"Unreadable file in {label}: {path.name}: {exc}") from exc

    @staticmethod
    def _validate_source_symlink(root: Path, path: Path, target: str) -> None:
        if os.path.isabs(target):
            raise BackupError(f"Absolute symlink is not backup-safe: {path}")
        resolved_target = (path.parent / target).resolve(strict=False)
        try:
            common = os.path.commonpath((str(root), str(resolved_target)))
        except ValueError as exc:
            raise BackupError(f"Invalid symlink target: {path}") from exc
        if common != str(root):
            raise BackupError(f"Symlink leaves backup source: {path}")

    def write_to(self, archive: tarfile.TarFile) -> None:
        if self.archive_root is not None:
            self._assert_unchanged(self.source, self.root_entry)
            root_info = archive.gettarinfo(
                str(self.source),
                arcname=str(self.archive_root),
            )
            root_info.type = tarfile.DIRTYPE
            root_info.linkname = ""
            archive.addfile(root_info)
            self._assert_unchanged(self.source, self.root_entry)
        for entry in self.entries:
            source_path = self.source.joinpath(*entry.relative.parts)
            self._assert_unchanged(source_path, entry)
            archive_name = (
                self.archive_root / entry.relative
                if self.archive_root is not None
                else entry.relative
            )
            info = archive.gettarinfo(str(source_path), arcname=str(archive_name))
            if entry.kind == "file":
                info.type = tarfile.REGTYPE
                info.linkname = ""
                with source_path.open("rb") as source:
                    archive.addfile(info, source)
            else:
                archive.addfile(info)
            self._assert_unchanged(source_path, entry)

    def verify_stable(self) -> None:
        current = TreePlan.capture(
            self.label,
            self.source,
            str(self.archive_root) if self.archive_root is not None else None,
            strict_files=self.strict_files,
        )
        if (
            current.root_entry != self.root_entry
            or current.entries != self.entries
        ):
            raise BackupError(f"{self.label} changed while it was being archived")

    @staticmethod
    def _assert_unchanged(path: Path, expected: TreeEntry) -> None:
        try:
            metadata = path.lstat()
            target = os.readlink(path) if stat.S_ISLNK(metadata.st_mode) else None
        except OSError as exc:
            raise BackupError(f"Backup source changed: {path}: {exc}") from exc
        actual = TreeEntry(
            relative=expected.relative,
            mode=metadata.st_mode,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            link_target=target,
        )
        if actual != expected:
            raise BackupError(f"Backup source changed: {path}")


class GpgRecipient:
    """Validated public-key fingerprint loaded from a small operator file."""

    def __init__(self, fingerprint: str) -> None:
        normalized = fingerprint.upper()
        if not _FINGERPRINT.fullmatch(normalized):
            raise BackupError(
                "Recipient file must contain one full 40- or 64-hex fingerprint"
            )
        self.fingerprint = normalized

    @classmethod
    def from_file(cls, path: Path) -> "GpgRecipient":
        try:
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or metadata.st_size > 4096
            ):
                raise BackupError("Recipient file must be a small regular file")
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as source:
                opened = os.fstat(source.fileno())
                if (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                ) != (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                ):
                    raise BackupError("Recipient file changed while it was opened")
                payload = source.read(4097)
            values = [
                line.strip()
                for line in payload.decode("ascii").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
        except (OSError, UnicodeError) as exc:
            raise BackupError(f"Cannot read recipient file: {exc}") from exc
        if len(values) != 1:
            raise BackupError("Recipient file must contain exactly one fingerprint")
        return cls(values[0])


class OpenPgpArchive:
    """Streaming tar -> zstd -> GPG writer and safe inverse reader."""

    def __init__(self, gpg: str = "gpg", zstd: str = "zstd") -> None:
        self.gpg = gpg
        self.zstd = zstd

    def verify_public_recipient(self, recipient: GpgRecipient) -> None:
        command = [
            self.gpg,
            "--no-options",
            "--batch",
            "--no-tty",
            "--with-colons",
            "--fingerprint",
            "--list-keys",
            recipient.fingerprint,
        ]
        output = self._capture(command)
        fingerprints = {
            fields[9].upper()
            for line in output.decode("utf-8", errors="replace").splitlines()
            if (fields := line.split(":"))[0] == "fpr" and len(fields) > 9
        }
        if recipient.fingerprint not in fingerprints:
            raise BackupError("Recipient public key fingerprint was not found")

    def encrypt_trees(
        self,
        plans: Sequence[TreePlan],
        recipient: GpgRecipient,
        destination: Path,
    ) -> None:
        if destination.exists():
            raise BackupError(f"Encrypted archive already exists: {destination}")
        gpg_error = tempfile.TemporaryFile()
        zstd_error = tempfile.TemporaryFile()
        gpg_process: subprocess.Popen[bytes] | None = None
        zstd_process: subprocess.Popen[bytes] | None = None
        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            output = os.fdopen(descriptor, "wb")
            with output:
                gpg_process = subprocess.Popen(
                    [
                        self.gpg,
                        "--no-options",
                        "--batch",
                        "--no-tty",
                        "--trust-model",
                        "always",
                        "--compress-algo",
                        "none",
                        "--recipient",
                        recipient.fingerprint,
                        "--encrypt",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=gpg_error,
                )
                if gpg_process.stdin is None:
                    raise BackupError("GPG encryption pipe is unavailable")
                zstd_process = subprocess.Popen(
                    [self.zstd, "--quiet", "--fast=1", "--threads=0", "--stdout"],
                    stdin=subprocess.PIPE,
                    stdout=gpg_process.stdin,
                    stderr=zstd_error,
                )
                gpg_process.stdin.close()
                if zstd_process.stdin is None:
                    raise BackupError("zstd compression pipe is unavailable")
                try:
                    with tarfile.open(
                        fileobj=zstd_process.stdin,
                        mode="w|",
                        format=tarfile.PAX_FORMAT,
                    ) as archive:
                        for plan in plans:
                            plan.write_to(archive)
                finally:
                    zstd_process.stdin.close()
                zstd_status = zstd_process.wait()
                gpg_status = gpg_process.wait()
                if zstd_status != 0:
                    raise self._pipeline_error("zstd compression", zstd_error)
                if gpg_status != 0:
                    raise self._pipeline_error("GPG encryption", gpg_error)
                for plan in plans:
                    plan.verify_stable()
                output.flush()
                os.fsync(output.fileno())
        except (OSError, tarfile.TarError, BrokenPipeError) as exc:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            destination.unlink(missing_ok=True)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Cannot create encrypted archive: {exc}") from exc
        except BaseException:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            destination.unlink(missing_ok=True)
            raise
        finally:
            gpg_error.close()
            zstd_error.close()

    def decrypt_to_directory(self, encrypted: Path, destination: Path) -> None:
        gpg_error = tempfile.TemporaryFile()
        zstd_error = tempfile.TemporaryFile()
        gpg_process: subprocess.Popen[bytes] | None = None
        zstd_process: subprocess.Popen[bytes] | None = None
        try:
            gpg_process = subprocess.Popen(
                [
                    self.gpg,
                    "--no-options",
                    "--batch",
                    "--no-tty",
                    "--decrypt",
                    str(encrypted),
                ],
                stdout=subprocess.PIPE,
                stderr=gpg_error,
            )
            if gpg_process.stdout is None:
                raise BackupError("GPG decryption pipe is unavailable")
            zstd_process = subprocess.Popen(
                [self.zstd, "--quiet", "--decompress", "--stdout"],
                stdin=gpg_process.stdout,
                stdout=subprocess.PIPE,
                stderr=zstd_error,
            )
            gpg_process.stdout.close()
            if zstd_process.stdout is None:
                raise BackupError("zstd decompression pipe is unavailable")
            with tarfile.open(
                fileobj=zstd_process.stdout,
                mode="r|*",
                tarinfo=BoundedTarInfo,
            ) as archive:
                SafeTarExtractor.extract_stream(archive, destination)
            zstd_process.stdout.close()
            zstd_status = zstd_process.wait()
            gpg_status = gpg_process.wait()
            if gpg_status != 0:
                raise self._pipeline_error("GPG decryption", gpg_error)
            if zstd_status != 0:
                raise self._pipeline_error("zstd decompression", zstd_error)
        except (OSError, tarfile.TarError, BrokenPipeError) as exc:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Cannot decrypt backup archive: {exc}") from exc
        except BaseException:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            raise
        finally:
            gpg_error.close()
            zstd_error.close()

    def read_member_bytes(
        self,
        encrypted: Path,
        member_names: set[str],
        *,
        max_member_bytes: int = 1024 * 1024,
    ) -> dict[str, bytes]:
        """Authenticate a full archive stream and retain only bounded metadata.

        Restore uses this pass before creating plaintext beside its requested
        target.  The complete OpenPGP/zstd stream is consumed so GPG integrity
        validation is not deferred until after target isolation decisions.
        """

        if (
            not member_names
            or max_member_bytes < 1
            or any(
                str(self._safe_metadata_name(name)) != name
                for name in member_names
            )
        ):
            raise BackupError("Requested archive metadata paths are invalid")
        gpg_error = tempfile.TemporaryFile()
        zstd_error = tempfile.TemporaryFile()
        gpg_process: subprocess.Popen[bytes] | None = None
        zstd_process: subprocess.Popen[bytes] | None = None
        selected: dict[str, bytes] = {}
        try:
            gpg_process = subprocess.Popen(
                [
                    self.gpg,
                    "--no-options",
                    "--batch",
                    "--no-tty",
                    "--decrypt",
                    str(encrypted),
                ],
                stdout=subprocess.PIPE,
                stderr=gpg_error,
            )
            if gpg_process.stdout is None:
                raise BackupError("GPG metadata pipe is unavailable")
            zstd_process = subprocess.Popen(
                [self.zstd, "--quiet", "--decompress", "--stdout"],
                stdin=gpg_process.stdout,
                stdout=subprocess.PIPE,
                stderr=zstd_error,
            )
            gpg_process.stdout.close()
            if zstd_process.stdout is None:
                raise BackupError("zstd metadata pipe is unavailable")
            with tarfile.open(
                fileobj=zstd_process.stdout,
                mode="r|*",
                tarinfo=BoundedTarInfo,
            ) as archive:
                validator = _TarStreamValidator()
                for member in archive:
                    relative = validator.validate(member)
                    if relative is None:
                        continue
                    normalized = str(relative)
                    if normalized not in member_names:
                        continue
                    if normalized in selected:
                        raise BackupError(
                            f"Duplicate archive metadata entry: {normalized}"
                        )
                    if (
                        not member.isreg()
                        or member.size < 1
                        or member.size > max_member_bytes
                    ):
                        raise BackupError(
                            f"Archive metadata entry is invalid: {normalized}"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise BackupError(
                            f"Cannot read archive metadata: {normalized}"
                        )
                    with source:
                        payload = source.read(max_member_bytes + 1)
                    if len(payload) != member.size:
                        raise BackupError(
                            f"Archive metadata entry is truncated: {normalized}"
                        )
                    selected[normalized] = payload
            zstd_process.stdout.close()
            zstd_status = zstd_process.wait()
            gpg_status = gpg_process.wait()
            if gpg_status != 0:
                raise self._pipeline_error(
                    "GPG metadata decryption",
                    gpg_error,
                )
            if zstd_status != 0:
                raise self._pipeline_error(
                    "zstd metadata decompression",
                    zstd_error,
                )
            missing = sorted(member_names - set(selected))
            if missing:
                raise BackupError(
                    "Encrypted backup metadata is incomplete: "
                    + ", ".join(missing)
                )
            return selected
        except (OSError, tarfile.TarError, BrokenPipeError) as exc:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            if isinstance(exc, BackupError):
                raise
            raise BackupError(
                f"Cannot inspect encrypted backup metadata: {exc}"
            ) from exc
        except BaseException:
            self._terminate(zstd_process)
            self._terminate(gpg_process)
            raise
        finally:
            gpg_error.close()
            zstd_error.close()

    @staticmethod
    def _safe_metadata_name(name: str) -> PurePosixPath:
        return SafeTarExtractor.safe_relative(name)

    @staticmethod
    def _capture(command: Sequence[str]) -> bytes:
        try:
            completed = subprocess.run(
                list(command),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = (
                (exc.stderr or b"").decode("utf-8", errors="replace").strip()
                if isinstance(exc, subprocess.CalledProcessError)
                else str(exc)
            )
            raise BackupError(f"Cannot validate GPG recipient: {detail}") from exc
        return completed.stdout

    @staticmethod
    def _pipeline_error(label: str, stream: BinaryIO) -> BackupError:
        stream.seek(0)
        detail = stream.read().decode("utf-8", errors="replace").strip()
        return BackupError(f"{label} failed: {detail[-1200:] or 'unknown error'}")

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes] | None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class SafeTarExtractor:
    """Extract regular content while preventing path and link escapes."""

    @classmethod
    def extract_stream(cls, archive: tarfile.TarFile, destination: Path) -> None:
        root = destination.resolve(strict=True)
        validator = _TarStreamValidator()
        for member in archive:
            relative = validator.validate(member)
            if relative is None:
                continue
            target = root.joinpath(*relative.parts)
            cls._ensure_parent(root, target.parent)
            if member.isdir():
                target.mkdir(mode=0o700, exist_ok=True)
                continue
            if member.isreg():
                if target.exists() or target.is_symlink():
                    raise BackupError(f"Duplicate archive entry: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError(f"Cannot read archive entry: {member.name}")
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    member.mode & 0o700 or 0o600,
                )
                with source, os.fdopen(descriptor, "wb") as output:
                    remaining = member.size
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise BackupError(f"Truncated archive entry: {member.name}")
                        output.write(block)
                        remaining -= len(block)
                    output.flush()
                    os.fsync(output.fileno())
                continue
            if member.issym():
                cls._create_safe_symlink(root, target, member.linkname, member.name)
                continue
            if member.islnk():
                link_relative = cls.safe_relative(member.linkname)
                link_source = root.joinpath(*link_relative.parts)
                cls._assert_within(root, link_source)
                if not link_source.is_file() or link_source.is_symlink():
                    raise BackupError(f"Invalid hardlink source: {member.name}")
                os.link(link_source, target)
                continue
            raise BackupError(f"Special archive entry is forbidden: {member.name}")

    @classmethod
    def extract_file(cls, archive_path: Path, destination: Path) -> None:
        try:
            with tarfile.open(
                archive_path,
                mode="r:",
                tarinfo=BoundedTarInfo,
            ) as archive:
                cls.extract_stream(archive, destination)
        except (OSError, tarfile.TarError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"Cannot extract {archive_path.name}: {exc}") from exc

    @staticmethod
    def safe_relative(name: str) -> PurePosixPath:
        normalized = PurePosixPath(name)
        if (
            not name
            or "\x00" in name
            or normalized.is_absolute()
            or ".." in normalized.parts
        ):
            raise BackupError(f"Unsafe archive path: {name!r}")
        parts = tuple(part for part in normalized.parts if part not in {"", "."})
        if not parts:
            raise BackupError(f"Empty archive path: {name!r}")
        return PurePosixPath(*parts)

    @classmethod
    def _ensure_parent(cls, root: Path, parent: Path) -> None:
        cls._assert_within(root, parent)
        relative = parent.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise BackupError(f"Unsafe extraction parent: {current}")
            else:
                current.mkdir(mode=0o700)

    @staticmethod
    def _assert_within(root: Path, path: Path) -> None:
        try:
            common = os.path.commonpath((str(root), str(path.resolve(strict=False))))
        except ValueError as exc:
            raise BackupError(f"Extraction path is invalid: {path}") from exc
        if common != str(root):
            raise BackupError(f"Extraction path leaves target: {path}")

    @classmethod
    def _create_safe_symlink(
        cls, root: Path, target: Path, linkname: str, member_name: str
    ) -> None:
        if os.path.isabs(linkname):
            raise BackupError(f"Absolute symlink is forbidden: {member_name}")
        resolved_link = (target.parent / linkname).resolve(strict=False)
        cls._assert_within(root, resolved_link)
        if target.exists() or target.is_symlink():
            raise BackupError(f"Duplicate archive entry: {member_name}")
        os.symlink(linkname, target)
