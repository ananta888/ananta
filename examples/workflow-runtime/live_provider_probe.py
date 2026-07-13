"""Bounded optional probe for a local OpenAI-compatible provider.

LangChain is deliberately used only as a Runnable provider adapter.  This
process cannot schedule workflows, create Hub tasks, or control a runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda

PROFILE_PATH = Path(__file__).with_name("live-provider-profile.v1.json")


class LiveProviderProbeError(RuntimeError):
    """Stable fail-closed probe configuration or transport failure."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        raise LiveProviderProbeError("example_live_provider_redirect_forbidden")


def _profile() -> dict[str, Any]:
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if value.get("schema") != "ananta.workflow_runtime_example_live_provider_profile.v1":
        raise LiveProviderProbeError("example_live_provider_profile_invalid")
    langchain = value.get("langchain") or {}
    if (
        langchain.get("role") != "provider_adapter"
        or langchain.get("control_plane") is not False
        or langchain.get("task_queue_owner") is not False
        or langchain.get("workflow_scheduler") is not False
    ):
        raise LiveProviderProbeError("example_live_provider_role_boundary_invalid")
    return value


def _required_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise LiveProviderProbeError(f"example_live_provider_env_required:{name}")
    return value


def _endpoint(profile: dict[str, Any]) -> tuple[str, str]:
    config = profile["configuration"]
    base_url = _required_env(str(config["base_url_env"]))
    model = _required_env(str(config["model_env"]))
    parsed = urllib.parse.urlsplit(base_url)
    allowed = set(config.get("allowed_hostnames") or ())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in allowed
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise LiveProviderProbeError("example_live_provider_base_url_denied")
    return f"{base_url.rstrip('/')}/chat/completions", model


def _credential(profile: dict[str, Any]) -> str:
    env_name = str(profile["configuration"]["credential_file_env"])
    path = Path(_required_env(env_name))
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            raise LiveProviderProbeError("example_live_provider_credential_file_invalid")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LiveProviderProbeError("example_live_provider_credential_file_unreadable") from exc
    if not value or "\x00" in value:
        raise LiveProviderProbeError("example_live_provider_credential_file_invalid")
    return value


def _invoke(request: dict[str, Any]) -> dict[str, Any]:
    profile = request["profile"]
    endpoint, model = _endpoint(profile)
    limits = profile["limits"]
    timeout = max(1.0, min(float(limits["timeout_seconds"]), 60.0))
    maximum = max(1024, min(int(limits["maximum_response_bytes"]), 1_048_576))
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly: ananta-live-provider-ok",
                }
            ],
            "temperature": 0,
            "max_tokens": int(limits["max_tokens"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    http_request = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {_credential(profile)}",
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(http_request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise LiveProviderProbeError("example_live_provider_http_status_invalid")
            body = response.read(maximum + 1)
    except LiveProviderProbeError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise LiveProviderProbeError("example_live_provider_request_failed") from exc
    if len(body) > maximum:
        raise LiveProviderProbeError("example_live_provider_response_too_large")
    try:
        decoded = json.loads(body.decode("utf-8"))
        content = str(decoded["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise LiveProviderProbeError("example_live_provider_response_invalid") from exc
    if not content:
        raise LiveProviderProbeError("example_live_provider_response_empty")
    usage_value = decoded.get("usage") if isinstance(decoded, dict) else None
    usage = usage_value if isinstance(usage_value, dict) else {}
    return {
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_bytes": len(content.encode("utf-8")),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "model": model,
        "provider_host": urllib.parse.urlsplit(endpoint).hostname,
    }


def main() -> None:
    profile = _profile()
    started = time.monotonic()
    # RunnableLambda is an optional LangChain provider integration only.  The
    # immutable workflow plan and all control decisions remain outside it.
    result = RunnableLambda(_invoke).invoke({"profile": profile})
    evidence = {
        "schema": "ananta.workflow_runtime_example_live_provider_evidence.v1",
        "status": "passed",
        "classification": "optional_local_live",
        "profile_id": profile["profile_id"],
        "langchain_role": "provider_adapter",
        "control_plane": False,
        "task_queue_owner": False,
        "production_release_gate": False,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "result": result,
    }
    output = Path(os.getenv("ANANTA_EXAMPLE_EVIDENCE_DIR", "/evidence")) / str(profile["evidence"]["output_file"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()


__all__ = ["LiveProviderProbeError", "main"]
