"""Bounded exact-target transport for Worker Native artifact publication."""

import json
import math
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from ananta_contracts.file_credentials import read_file_managed_token
from ananta_contracts.native_artifact_transfer import (
    MAX_INPUT_HTTP_BYTES,
    MAX_RECEIPT_HTTP_BYTES,
    MAX_TRANSFER_HTTP_BYTES,
)
from worker.runtime.workflow_hub_http import open_hub_command
from worker.runtime.workflow_service_identity import WorkflowServiceIdentity


class NativeArtifactHttpTransport:
    def __init__(
        self, *, hub_url, bearer_token, worker_id, worker_url, endpoint_prefix="/api",
        timeout_seconds=15.0, ssl_context: ssl.SSLContext | None = None,
    ):
        parsed = urllib.parse.urlsplit(hub_url)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("native_artifact_hub_url_invalid") from exc
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or any(ord(c) < 33 for c in hub_url) or endpoint_prefix not in {"", "/api"}
        ):
            raise ValueError("native_artifact_hub_url_invalid")
        if (
            not isinstance(bearer_token, str) or not 32 <= len(bearer_token.encode()) <= 16_384
            or any(c.isspace() or ord(c) < 32 for c in bearer_token)
        ):
            raise ValueError("native_artifact_worker_token_invalid")
        identity = WorkflowServiceIdentity.optional(worker_id=worker_id, worker_url=worker_url)
        if identity is None:
            raise ValueError("native_artifact_worker_identity_required")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ValueError("native_artifact_http_timeout_invalid")
        self._base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._headers = {"Authorization": f"Bearer {bearer_token}", "Accept": "application/json", **identity.headers()}
        self._prefix, self._timeout, self._ssl_context = endpoint_prefix, timeout_seconds, ssl_context

    @classmethod
    def from_environment(cls, env=None):
        source = os.environ if env is None else env
        hub_url = source.get("ANANTA_WORKFLOW_HUB_URL") or source.get("ANANTA_LANGGRAPH_HUB_URL") or ""
        token_file = source.get("ANANTA_WORKFLOW_HUB_TOKEN_FILE") or source.get("ANANTA_LANGGRAPH_HUB_TOKEN_FILE") or ""
        if not hub_url and not token_file:
            return None
        if not hub_url or not token_file:
            raise ValueError("native_artifact_http_configuration_incomplete")
        token = read_file_managed_token(token_file)
        return cls(hub_url=hub_url, bearer_token=token, worker_id=source.get("AGENT_NAME", ""),
                   worker_url=source.get("AGENT_URL", ""))

    def request(self, *, method, path, payload: Mapping | None = None):
        input_request = re.fullmatch(
            r"/internal/tasks/[A-Za-z0-9_.%~-]+/native-artifact-inputs/[A-Za-z0-9_.%~-]+", path,
        )
        if (
            method not in {"GET", "POST"}
            or not (re.fullmatch(r"/internal/tasks/[A-Za-z0-9_.%~-]+/native-artifacts", path) or input_request)
            or input_request and method != "GET"
        ):
            raise ValueError("native_artifact_http_target_invalid")
        if (method == "GET" and payload is not None) or (method == "POST" and not isinstance(payload, Mapping)):
            raise ValueError("native_artifact_http_payload_invalid")
        body = None if payload is None else json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if body is not None and len(body) > MAX_TRANSFER_HTTP_BYTES:
            raise ValueError("native_artifact_http_body_too_large")
        request = urllib.request.Request(
            self._base + self._prefix + path, data=body,
            headers={**self._headers, "Content-Type": "application/json"}, method=method,
        )
        try:
            response_limit = MAX_INPUT_HTTP_BYTES if input_request else MAX_RECEIPT_HTTP_BYTES
            with open_hub_command(request, timeout=self._timeout, ssl_context=self._ssl_context) as response:
                raw = response.read(response_limit + 1)
        except urllib.error.HTTPError as exc:
            reason = "native_artifact_http_redirect_denied" if 300 <= exc.code < 400 else "native_artifact_http_denied"
            exc.close()
            raise ValueError(reason) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ValueError("native_artifact_http_unavailable") from exc
        if len(raw) > response_limit:
            raise ValueError("native_artifact_http_response_too_large")
        try:
            decoded = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("native_artifact_http_response_invalid") from exc
        if not isinstance(decoded, Mapping) or not isinstance(decoded.get("data"), Mapping):
            raise ValueError("native_artifact_http_response_invalid")
        return dict(decoded["data"])
