"""Command-line surfaces for backup and isolated restore drills."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .backup_service import BackupRequest, BackupService
from .compose import ComposeAdapter, IsolatedPostgresRestoreDrill
from .errors import BackupError
from .restore_service import RestoreRequest, RestoreService

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_COMPOSE = (
    _REPOSITORY_ROOT / "docker" / "compose-next" / "compose.dev.ollama.yml"
)
_DEFAULT_ENV = _REPOSITORY_ROOT / ".env"


def backup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create an encrypted, no-clobber Ananta backup in WSL and copy only "
            "its ciphertext package to a Windows target."
        )
    )
    parser.add_argument("--wsl-target", type=Path, required=True)
    parser.add_argument("--windows-target", type=Path, required=True)
    parser.add_argument(
        "--recipient-file",
        type=Path,
        required=True,
        help="Small file containing exactly one full GPG public-key fingerprint.",
    )
    parser.add_argument("--compose-file", type=Path, default=_DEFAULT_COMPOSE)
    parser.add_argument("--env-file", type=Path, default=_DEFAULT_ENV)
    parser.add_argument("--name", dest="backup_name")
    parser.add_argument(
        "--include-ollama-models",
        action="store_true",
        help=(
            "Include only .ollama/models (disabled for fast routine backups; "
            "the Ollama host identity is never included)."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def restore_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Decrypt and verify an Ananta backup in a new/empty drill directory. "
            "No Docker volume or live service is modified."
        )
    )
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def backup_main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    arguments = backup_parser().parse_args(argv)
    try:
        service = BackupService(
            ComposeAdapter(arguments.compose_file, arguments.env_file)
        )
        result = service.run(
            BackupRequest(
                wsl_target=arguments.wsl_target,
                windows_target=arguments.windows_target,
                recipient_file=arguments.recipient_file,
                backup_name=arguments.backup_name,
                include_ollama_models=arguments.include_ollama_models,
                dry_run=arguments.dry_run,
            )
        )
    except (BackupError, OSError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "backup_name": result.backup_name,
                "dry_run": result.dry_run,
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
                "windows_package": str(result.windows_package),
                "wsl_package": str(result.wsl_package),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def restore_main(argv: Sequence[str] | None = None) -> int:
    os.umask(0o077)
    arguments = restore_parser().parse_args(argv)
    try:
        result = RestoreService(
            postgres_drill_factory=IsolatedPostgresRestoreDrill,
            forbidden_live_roots=(
                _REPOSITORY_ROOT,
            ),
        ).run(
            RestoreRequest(
                package=arguments.package,
                target=arguments.target,
                dry_run=arguments.dry_run,
            )
        )
    except (BackupError, OSError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "backup_name": result.backup_name,
                "ciphertext_sha256": result.sha256,
                "dry_run": result.dry_run,
                "target": str(result.target),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0
