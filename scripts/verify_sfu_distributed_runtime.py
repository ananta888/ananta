#!/usr/bin/env python3
"""Content-free structural verifier for the distributed SFU mode matrix."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence


EVIDENCE_ID = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def evaluate(
    *,
    directory_path: Path,
    compose_path: Path,
    redis_config_path: Path,
    evidence_ids: Sequence[str] = (),
    evidence_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    directory = json.loads(directory_path.read_text(encoding="utf-8"))
    compose = compose_path.read_text(encoding="utf-8")
    redis_config = redis_config_path.read_text(encoding="utf-8")
    modes = directory.get("modes") if isinstance(directory, dict) else None
    native = modes.get("livekit_native") if isinstance(modes, dict) else None
    extension = modes.get("hub_cluster_only") if isinstance(modes, dict) else None
    native_targets = native.get("targets") if isinstance(native, dict) else []
    extension_targets = extension.get("targets") if isinstance(extension, dict) else []
    structural = {
        "directory_versioned": isinstance(directory.get("directory_version"), int)
        and directory["directory_version"] > 0,
        "native_has_two_targets": isinstance(native_targets, list) and len(native_targets) >= 2,
        "extension_has_two_targets": isinstance(extension_targets, list) and len(extension_targets) >= 2,
        "hub_node_selection_forbidden": bool(native)
        and native.get("hub_selects_node") is False
        and all(target.get("selectable_by_hub") is False for target in native_targets)
        and bool(extension)
        and extension.get("hub_selects_node") is False,
        "redis_scope_isolated": native.get("redis_scope") == "sfu-broadcast-livekit-only"
        if isinstance(native, dict)
        else False,
        "redis_tls_only": "port 0" in redis_config and "tls-port 6379" in redis_config,
        "redis_acl_required": "aclfile " in redis_config and "protected-mode yes" in redis_config,
        "compose_native_pair": all(
            name in compose
            for name in ("sfu-broadcast-livekit-native-a", "sfu-broadcast-livekit-native-b")
        ),
        "compose_extension_pair": all(
            name in compose for name in ("sfu-runtime-agent-a", "sfu-runtime-agent-b")
        ),
        "resource_limits_present": compose.count("pids:") >= 5,
    }
    valid_evidence = sorted({value for value in evidence_ids if EVIDENCE_ID.fullmatch(value)})
    manifest = evidence_manifest if isinstance(evidence_manifest, dict) else {}
    source_id = manifest.get("source_id")
    run_id = manifest.get("run_id")
    source_digest = manifest.get("source_digest")
    run_digest = manifest.get("run_digest")
    evidence_ready = (
        manifest.get("verified") is True
        and source_id in valid_evidence
        and run_id in valid_evidence
        and isinstance(source_id, str)
        and source_id.startswith("SRC_")
        and isinstance(run_id, str)
        and run_id.startswith("RUN_")
        and isinstance(source_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", source_digest) is not None
        and isinstance(run_digest, str)
        and re.fullmatch(r"[0-9a-f]{64}", run_digest) is not None
    )
    structural_ready = all(structural.values())
    reasons = []
    if not structural_ready:
        reasons.append("sfu_distributed_structure_incomplete")
    if not evidence_ready:
        reasons.append("sfu_distributed_runtime_evidence_missing")
    if "image: redis:7.4.2-alpine\n" in compose:
        reasons.append("sfu_redis_image_digest_unpinned")
    ready = structural_ready and evidence_ready and "sfu_redis_image_digest_unpinned" not in reasons
    return {
        "schema_version": 1,
        "ready": ready,
        "reason_codes": reasons,
        "structural_checks": structural,
        "evidence_ids": valid_evidence,
        "capabilities": {
            "multi_node": ready,
            "distributed_capacity": ready,
            "rolling_drain": ready,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("config/sfu_broadcast_cluster_directory.json"))
    parser.add_argument("--compose", type=Path, default=Path("docker-compose.sfu-broadcast.yml"))
    parser.add_argument("--redis-config", type=Path, default=Path("config/redis/sfu-broadcast.conf"))
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--evidence-manifest", type=Path)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    manifest = None
    if args.evidence_manifest is not None:
        manifest = json.loads(args.evidence_manifest.read_text(encoding="utf-8"))
    result = evaluate(
        directory_path=args.directory,
        compose_path=args.compose,
        redis_config_path=args.redis_config,
        evidence_ids=args.evidence_id,
        evidence_manifest=manifest,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 1 if args.require_ready and not result["ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
