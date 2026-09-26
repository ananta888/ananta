"""Worker side of a Hub-delegated CodeCompass ``chunks`` layer build.

The task carries only a ticket. The handler reads the job spec from the Hub,
pulls the job's redacted file contents, builds one layer with
``ChunkLayerBuilder``, uploads it and returns a result that echoes the
ticket's binding. It never reads the repository, never touches heads and
never orchestrates other work: the Hub decides what happens with the layer.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from ananta_contracts.codecompass_layer_job import (
    CONTENT_PATH,
    CONTEXT_KEY,
    JOB_PATH,
    JOB_SPEC_SCHEMA,
    LAYER_MEDIA_TYPE,
    LAYER_PATH,
    RESULT_SCHEMA,
    TASK_KIND,
    JobTicket,
    canonical_digest,
)
from worker.incremental_index.chunk_builder import ChunkLayerBuilder
from worker.incremental_index.layer_store import ArtifactLayerStore
from worker.incremental_index.snapshot_diff import FileChange

_CHANGE_FIELDS = ("operation", "path", "new_path", "old_content_sha256", "new_content_sha256", "old_byte_size",
                  "new_byte_size")


class LayerJobHubClientPort(Protocol):
    def fetch_spec(self, task_id: str) -> Mapping[str, Any]: ...

    def fetch_content(self, task_id: str, part: int) -> Mapping[str, Any]: ...

    def upload_layer(self, task_id: str, blob: bytes) -> Mapping[str, Any]: ...


class CodeCompassLayerJobHandler:
    """``TaskHandler`` for ``codecompass_layer_build``."""

    def __init__(self, client: LayerJobHubClientPort, builder: ChunkLayerBuilder | None = None) -> None:
        self._client = client
        self._builder = builder or ChunkLayerBuilder()

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        ticket = self._ticket(kwargs)
        return {
            "proposal_id": f"{ticket.task_id}-proposal",
            "strategy_id": "deterministic_handler",
            "command": None,
            "tool_calls": [{"name": TASK_KIND, "arguments": {"task_id": ticket.task_id}}],
            "safety_flags": {"worker_only": True, "worker_orchestration_forbidden": True,
                             "human_approval_required": False},
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        try:
            ticket = self._ticket(kwargs)
        except Exception as error:  # noqa: BLE001 -- an unbindable task is a coded failure
            return {"schema": RESULT_SCHEMA, "status": "failed", "task_id": str(kwargs.get("tid") or "")[:191],
                    "reason_code": _reason(error)}
        spec: Mapping[str, Any] = {}
        try:
            spec = self._spec(ticket)
            layer = self._build(ticket, spec)
            uploaded = dict(self._client.upload_layer(ticket.task_id, _blob(layer)) or {})
            if uploaded.get("layer_id") != layer["layer_id"]:
                raise ValueError("codecompass_layer_upload_id_mismatch")
        except Exception as error:  # noqa: BLE001 -- the Hub needs a bound failure, not a crash
            return {**_binding(ticket, spec), "status": "failed", "reason_code": _reason(error)}
        artifact_set = {"chunks": {"content_digest": layer["layer_id"], "record_count": layer["record_count"],
                                   "tombstone_count": layer["tombstone_count"]}}
        return {**_binding(ticket, spec), "status": "completed", "artifact_kinds": ["chunks"],
                "artifact_set": artifact_set, "artifact_set_digest": canonical_digest(artifact_set)}

    @staticmethod
    def _ticket(kwargs: Mapping[str, Any]) -> JobTicket:
        task = kwargs.get("task")
        if not isinstance(task, Mapping) or str(task.get("task_kind") or "").strip() != TASK_KIND:
            raise ValueError("codecompass_layer_task_kind_invalid")
        context = task.get("worker_execution_context")
        return JobTicket.from_mapping(context.get(CONTEXT_KEY) if isinstance(context, Mapping) else None)

    def _spec(self, ticket: JobTicket) -> Mapping[str, Any]:
        spec = dict(self._client.fetch_spec(ticket.task_id) or {})
        if (
            spec.get("schema") != JOB_SPEC_SCHEMA
            or spec.get("task_id") != ticket.task_id
            or spec.get("intent_digest") != ticket.intent_digest
            or spec.get("assignment_id") != ticket.assignment_id
            or spec.get("dispatch_lease_id") != ticket.dispatch_lease_id
            or list(spec.get("artifact_kinds") or []) != ["chunks"]
        ):
            raise ValueError("codecompass_layer_job_spec_binding_invalid")
        return spec

    def _build(self, ticket: JobTicket, spec: Mapping[str, Any]) -> dict[str, Any]:
        texts: dict[str, str] = {}
        for part in range(int(spec.get("content_parts") or 0)):
            payload = dict(self._client.fetch_content(ticket.task_id, part) or {})
            texts.update({str(key): str(value) for key, value in dict(payload.get("texts") or {}).items()})
        changes = [FileChange(**{key: item.get(key) for key in _CHANGE_FIELDS})
                   for item in list(spec.get("file_changes") or [])]
        targets = [change.new_path or change.path for change in changes]
        if any(not path for path in targets) or len(set(targets)) != len(targets):
            # Never build a layer from a spec whose paths collapsed (e.g. masked in transit).
            raise ValueError("codecompass_layer_job_spec_paths_invalid")
        content: dict[str, str] = {}
        for change in changes:
            if change.operation == "delete":
                continue
            digest = str(change.new_content_sha256 or "")
            if digest not in texts:
                raise ValueError("codecompass_layer_content_missing")
            content[change.new_path or change.path] = texts[digest]
        layer = self._builder.build(
            changes=changes,
            content=content,
            prior_ids_by_path=dict(spec.get("prior_record_ids_by_path") or {}),
            parent_layer_id=spec.get("parent_layer_id") or None,
            snapshot_revision=str(spec["input_revision"]),
            changeset_id=str(spec.get("changeset_id") or ""),
            compatibility_key=dict(spec.get("compatibility_key") or {}),
        )
        layer["layer_id"] = ArtifactLayerStore.compute_layer_id(layer)
        return layer


def _binding(ticket: JobTicket, spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "task_id": ticket.task_id,
        "assignment_id": ticket.assignment_id,
        "dispatch_lease_id": ticket.dispatch_lease_id,
        "intent_digest": ticket.intent_digest,
        "run_id": ticket.run_id,
        "input_revision": str(spec.get("input_revision") or ""),
        "profile_digest": str(spec.get("profile_digest") or ""),
    }


def _blob(layer: Mapping[str, Any]) -> bytes:
    return gzip.compress(json.dumps(dict(layer), sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _reason(error: Exception) -> str:
    text = str(error).strip()
    if text.startswith("codecompass_layer_") and len(text) <= 160:
        return text
    return f"codecompass_layer_build_failed:{type(error).__name__}"


class HttpLayerJobHubClient:
    """``LayerJobHubClientPort`` over the Hub's internal layer-job endpoints."""

    def __init__(self, *, hub_url: str, headers: Mapping[str, str], timeout: float = 120.0) -> None:
        hub_url = str(hub_url or "").strip().rstrip("/")
        if not hub_url.startswith(("http://", "https://")):
            raise ValueError("codecompass_layer_hub_url_invalid")
        self._hub = hub_url
        self._headers = dict(headers)
        self._timeout = float(timeout)

    def _url(self, template: str, task_id: str, query: str = "") -> str:
        return self._hub + template.format(task_id=urllib.parse.quote(task_id, safe="")) + query

    def _open(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as error:
            raise ValueError(f"codecompass_layer_hub_http_{int(error.code or 0)}") from None

    def _json(self, request: urllib.request.Request) -> dict[str, Any]:
        payload = json.loads(self._open(request))
        return dict(payload.get("data") if isinstance(payload, Mapping) and "data" in payload else payload)

    def fetch_spec(self, task_id: str) -> Mapping[str, Any]:
        return self._json(urllib.request.Request(self._url(JOB_PATH, task_id), headers=self._headers))

    def fetch_content(self, task_id: str, part: int) -> Mapping[str, Any]:
        url = self._url(CONTENT_PATH, task_id, f"?part={int(part)}")
        return json.loads(self._open(urllib.request.Request(url, headers=self._headers)))

    def upload_layer(self, task_id: str, blob: bytes) -> Mapping[str, Any]:
        request = urllib.request.Request(self._url(LAYER_PATH, task_id), data=blob, method="PUT",
                                         headers={**self._headers, "Content-Type": LAYER_MEDIA_TYPE})
        return self._json(request)
