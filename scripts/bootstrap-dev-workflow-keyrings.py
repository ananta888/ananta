#!/usr/bin/env python3
"""Create or validate local Compose workflow keyrings without exposing Hub secrets."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.dev_workflow_keyring_contract import (  # noqa: E402
    _LEGACY_ALL_DOCUMENTS as _LEGACY_ALL_DOCUMENTS,
)
from scripts.dev_workflow_keyring_contract import (  # noqa: E402
    DevWorkflowKeyringBootstrapError,
)
from scripts.dev_workflow_keyring_filesystem import (  # noqa: E402
    _assign_host_ownership,
    _paths,
)
from scripts.dev_workflow_keyring_orchestrator import bootstrap  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or validate local-only workflow runtime keyrings.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Absolute directory mounted only into the local Compose stack.",
    )
    parser.add_argument(
        "--alpha-worker-id",
        default="ananta-worker-1",
        help="Registered identity for the local alpha Worker.",
    )
    parser.add_argument(
        "--beta-worker-id",
        default="ananta-worker-2",
        help="Registered identity for the local beta Worker.",
    )
    parser.add_argument(
        "--owner-uid",
        type=int,
        help=("Optional WSL host UID that should own the generated bind-mounted credentials."),
    )
    parser.add_argument(
        "--owner-gid",
        type=int,
        help=("Optional WSL host GID that should own the generated bind-mounted credentials."),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if (args.owner_uid is None) != (args.owner_gid is None):
            raise DevWorkflowKeyringBootstrapError("owner UID and GID must be configured together")
        result = bootstrap(
            args.root,
            alpha_worker_id=args.alpha_worker_id,
            beta_worker_id=args.beta_worker_id,
        )
        if args.owner_uid is not None and args.owner_gid is not None:
            _assign_host_ownership(
                _paths(args.root),
                owner_uid=args.owner_uid,
                owner_gid=args.owner_gid,
            )
    except DevWorkflowKeyringBootstrapError as exc:
        print(f"workflow keyring bootstrap failed: {exc}", file=os.sys.stderr)
        return 64
    print(f"development workflow keyrings {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
