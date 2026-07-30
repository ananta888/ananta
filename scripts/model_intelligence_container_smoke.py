#!/usr/bin/env python3
"""Built-image hardening smoke for a model-intelligence execution worker.

The script consumes an already built image. It performs no build or download.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid

_CONTAINER_PROBE = r"""
import errno
import json
import os
import socket
from pathlib import Path

expected_uid = int(os.environ.get("ANANTA_SMOKE_EXPECTED_UID", "0"))
uid = os.geteuid()
if uid == 0 or (expected_uid and uid != expected_uid):
    raise SystemExit("container_user_policy_failed")

root_read_only = False
try:
    Path("/.ananta-model-intelligence-smoke").write_text("forbidden", encoding="utf-8")
except OSError as exc:
    root_read_only = exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
if not root_read_only:
    raise SystemExit("container_root_not_read_only")

network_blocked = False
connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
connection.settimeout(0.25)
try:
    connection.connect(("1.1.1.1", 53))
except OSError:
    network_blocked = True
finally:
    connection.close()
if not network_blocked:
    raise SystemExit("container_egress_available")

cleanup_root = Path("/tmp/model-intelligence-smoke")
cleanup_root.mkdir()
artifact = cleanup_root / "partial-artifact"
artifact.write_bytes(b"bounded")
artifact.unlink()
cleanup_root.rmdir()
if cleanup_root.exists():
    raise SystemExit("container_cleanup_failed")

print(json.dumps({
    "cleanup": True,
    "network_blocked": network_blocked,
    "read_only_root": root_read_only,
    "uid": uid,
}, sort_keys=True))
"""


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Already-built local image reference")
    parser.add_argument("--expected-uid", type=int, default=10002)
    args = parser.parse_args()
    if args.expected_uid <= 0:
        parser.error("--expected-uid must be positive")

    image = _run(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"])
    image_id = image.stdout.strip()
    if not image_id.startswith("sha256:"):
        raise SystemExit("image_identity_unavailable")

    name = f"ananta-mi-smoke-{uuid.uuid4().hex[:12]}"
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--tmpfs",
            "/run:rw,noexec,nosuid,nodev,size=16m",
            "--env",
            f"ANANTA_SMOKE_EXPECTED_UID={args.expected_uid}",
            "--entrypoint",
            "python",
            args.image,
            "-c",
            _CONTAINER_PROBE,
        ]
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    lingering = _run(["docker", "container", "inspect", name], check=False)
    if lingering.returncode == 0:
        raise SystemExit("container_cleanup_failed")
    report = {
        "schema": "ananta.model-intelligence.container-hardening-smoke.v1",
        "image": args.image,
        "image_id": image_id,
        "status": "passed",
        **payload,
        "container_removed": True,
    }
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
