from __future__ import annotations

import json
from typing import Any

from worker.core.execution_envelope import ExecutionEnvelope
from worker.core.sanitizer import OutputSanitizer

_SAN = OutputSanitizer()


def build_governed_system_prompt(
    *,
    envelope: ExecutionEnvelope,
    allowed_mode: str,
    denied_operations: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> str:
    denied = list(denied_operations or envelope.denied_operations)
    payload = {
        "adapter_role": "external_ananta_worker",
        "allowed_mode": str(allowed_mode),
        "denied_operations": denied,
        "rules": [
            "You are an external Ananta worker and cannot claim side effects.",
            "Output must be strict JSON matching the provided schema.",
            "Do not execute shell commands or claim execution.",
            "Do not write files, apply patches, mutate memory, create cron jobs or mutate tasks directly.",
            "Do not use shell/file/browser/MCP/tool autonomy in phase1.",
            "If information is missing, return uncertainty and bounded assumptions.",
        ],
        "schema": output_schema or {},
    }
    return _SAN.sanitize(json.dumps(payload, ensure_ascii=True, separators=(",", ":"))).text
