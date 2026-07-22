#!/usr/bin/env python3
"""Verify an SFU broadcast evidence manifest without following symlinks."""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from agent.services.release_evidence_provenance_service import (
    ArtifactReadError,
    ReasonCode,
    VerificationContext,
    failed_report,
    sha256_bytes,
    verify_evidence_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "artifacts/test-gates/sfu-broadcast-evidence-manifest.json"
DEFAULT_GATE_CONFIG = ROOT / "config/release/sfu_broadcast_gate_manifest.json"


class RepositoryArtifactReader:
    """Linux dir-fd reader that rejects traversal and every symlink component."""

    def __init__(self, repository_root: Path, allowed_root: str) -> None:
        self._root = repository_root.resolve(strict=True)
        if not self._root.is_dir():
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT)
        if allowed_root == ".":
            self._allowed_parts: tuple[str, ...] = ()
        else:
            self._allowed_parts = self._path_parts(allowed_root)

    def read(self, repository_relative_path: str) -> bytes:
        parts = self._path_parts(repository_relative_path)
        if self._allowed_parts and parts[: len(self._allowed_parts)] != self._allowed_parts:
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT)

        directory_fds: list[int] = []
        file_fd: int | None = None
        try:
            root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fds.append(os.open(self._root, root_flags))
            for part in parts[:-1]:
                metadata = os.stat(part, dir_fd=directory_fds[-1], follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise ArtifactReadError(ReasonCode.ARTIFACT_SYMLINK_FORBIDDEN)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT)
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                directory_fds.append(os.open(part, flags, dir_fd=directory_fds[-1]))

            metadata = os.stat(parts[-1], dir_fd=directory_fds[-1], follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode):
                raise ArtifactReadError(ReasonCode.ARTIFACT_SYMLINK_FORBIDDEN)
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactReadError(ReasonCode.ARTIFACT_NOT_REGULAR_FILE)
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fds[-1],
            )
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ArtifactReadError(ReasonCode.ARTIFACT_NOT_REGULAR_FILE)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        except ArtifactReadError:
            raise
        except FileNotFoundError as exc:
            raise ArtifactReadError(ReasonCode.ARTIFACT_MISSING) from exc
        except PermissionError as exc:
            raise ArtifactReadError(ReasonCode.ARTIFACT_UNREADABLE) from exc
        except OSError as exc:
            raise ArtifactReadError(ReasonCode.ARTIFACT_READ_FAILED) from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(directory_fds):
                os.close(descriptor)

    @staticmethod
    def _path_parts(value: str) -> tuple[str, ...]:
        if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_INVALID)
        raw_parts = value.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_INVALID)
        path = PurePosixPath(value)
        if path.is_absolute():
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_INVALID)
        return tuple(path.parts)


class Ed25519AttestationVerifier:
    """Real detached Ed25519 verification for an explicitly configured profile."""

    def __init__(self, reader: RepositoryArtifactReader, trusted_keys: Sequence[Mapping[str, Any]]) -> None:
        self._reader = reader
        self._trusted_keys = {
            str(key["key_id"]): (str(key["public_key_path"]), str(key["public_key_sha256"]))
            for key in trusted_keys
            if isinstance(key, Mapping)
            and {"key_id", "public_key_path", "public_key_sha256"}.issubset(key)
        }

    def verify(self, payload: bytes, *, key_id: str, signature: str) -> bool:
        key_binding = self._trusted_keys.get(key_id)
        if key_binding is None:
            return False
        try:
            key_bytes = self._reader.read(key_binding[0])
            if sha256_bytes(key_bytes) != key_binding[1]:
                return False
            signature_bytes = base64.b64decode(signature, validate=True)
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            public_key = serialization.load_pem_public_key(key_bytes)
            if not isinstance(public_key, Ed25519PublicKey):
                return False
            public_key.verify(signature_bytes, payload)
            return True
        except Exception:
            return False


def _repo_relative(root: Path, path: Path) -> str:
    if path.is_absolute():
        try:
            return path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ArtifactReadError(ReasonCode.ARTIFACT_PATH_OUTSIDE_ROOT) from exc
    return path.as_posix()


def _load_json(reader: RepositoryArtifactReader, path: str) -> Mapping[str, Any]:
    try:
        document = json.loads(reader.read(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("json_input_invalid") from exc
    if not isinstance(document, Mapping):
        raise ValueError("json_input_invalid")
    return document


def _parse_digest_arguments(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        identifier, separator, digest = value.partition("=")
        if not separator or not identifier or identifier in result or len(digest) != 64:
            raise ValueError("digest_argument_invalid")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("digest_argument_invalid")
        result[identifier] = digest
    return result


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _attestation_verifiers(
    gate_configuration: Mapping[str, Any],
    repository_reader: RepositoryArtifactReader,
) -> dict[str, Ed25519AttestationVerifier]:
    result: dict[str, Ed25519AttestationVerifier] = {}
    profiles = gate_configuration.get("attestation_profiles")
    if not isinstance(profiles, Mapping):
        return result
    for profile_id, profile in profiles.items():
        if isinstance(profile, Mapping) and profile.get("algorithm") == "ed25519":
            trusted_keys = profile.get("trusted_keys")
            if isinstance(trusted_keys, list):
                result[str(profile_id)] = Ed25519AttestationVerifier(repository_reader, trusted_keys)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify source-bound SFU broadcast evidence.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE_CONFIG)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--git-source-digest", required=True)
    parser.add_argument("--config-digest", action="append", default=[], metavar="ID=SHA256")
    parser.add_argument("--lockfile-digest", action="append", default=[], metavar="ID=SHA256")
    parser.add_argument("--image-digest", action="append", default=[], metavar="ID=SHA256")
    parser.add_argument("--infrastructure-digest", action="append", default=[], metavar="ID=SHA256")
    parser.add_argument("--now", help="Timezone-aware ISO-8601 verification time.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = args.repo_root.resolve(strict=True)
        repository_reader = RepositoryArtifactReader(root, ".")
        gate_configuration = _load_json(repository_reader, _repo_relative(root, args.gate_config))
        manifest = _load_json(repository_reader, _repo_relative(root, args.manifest))
        artifact_root = gate_configuration.get("artifact_root")
        artifact_reader = RepositoryArtifactReader(root, artifact_root if isinstance(artifact_root, str) else ".")
        context = VerificationContext(
            git_source_digest=args.git_source_digest,
            config_digests=_parse_digest_arguments(args.config_digest),
            lockfile_digests=_parse_digest_arguments(args.lockfile_digest),
            image_digests=_parse_digest_arguments(args.image_digest),
            infrastructure_digests=_parse_digest_arguments(args.infrastructure_digest),
        )
        report = verify_evidence_manifest(
            manifest,
            gate_configuration=gate_configuration,
            context=context,
            artifact_reader=artifact_reader,
            now=_parse_now(args.now),
            attestation_verifiers=_attestation_verifiers(gate_configuration, repository_reader),
        )
    except ArtifactReadError:
        report = failed_report(ReasonCode.MANIFEST_INPUT_INVALID)
    except (OSError, ValueError):
        report = failed_report(ReasonCode.VERIFIER_ARGUMENT_INVALID)

    print(json.dumps(report.as_document(), sort_keys=True, separators=(",", ":")))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
