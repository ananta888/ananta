"""What a Worker may read and deliver for one active CodeCompass layer job.

Everything is scoped to a dispatch in state ``dispatched``: the job spec, the
redacted contents of exactly that job's files (in the parts fixed at
dispatch time) and one uploaded layer that must match the job's snapshot,
parent and kind. The upload only stores the verified blob; the head moves
later, when the Hub admits the task result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from agent.services.codecompass_layer_dispatch_adapters import job_spec
from ananta_contracts.codecompass_layer_job import CONTENT_PART_SCHEMA, valid_task_id


class LayerJobNotFound(LookupError):
    pass


class CodeCompassLayerJobGateway:
    def __init__(self, *, job_lookup: Callable[[str], Mapping[str, Any] | None], contents: Any, layers: Any) -> None:
        self._job_lookup = job_lookup
        self._contents = contents
        self._layers = layers

    def _dispatch(self, task_id: str) -> Mapping[str, Any]:
        record = self._job_lookup(valid_task_id(task_id))
        if record is None:
            raise LayerJobNotFound("codecompass_layer_job_not_active")
        return record

    def spec(self, task_id: str) -> dict[str, Any]:
        return job_spec(self._dispatch(task_id))

    def content_part(self, task_id: str, part: int) -> dict[str, Any]:
        parts = list(dict(self._dispatch(task_id).get("binding") or {}).get("content_parts") or [])
        if not 0 <= int(part) < len(parts):
            raise LayerJobNotFound("codecompass_layer_content_part_unknown")
        texts = {}
        for digest in parts[int(part)]:
            text = self._contents.get(digest)
            if text is None:
                raise LayerJobNotFound("codecompass_layer_content_missing")
            texts[digest] = text
        return {"schema": CONTENT_PART_SCHEMA, "task_id": task_id, "part": int(part), "parts": len(parts),
                "texts": texts}

    def receive_layer(self, task_id: str, blob: bytes) -> dict[str, Any]:
        spec = job_spec(self._dispatch(task_id))
        layer_id, created = self._layers.store_verified_blob(blob)
        layer = self._layers.get_layer(layer_id) or {}
        if (
            str(layer.get("snapshot_revision") or "") != spec["input_revision"]
            or (layer.get("parent_layer_id") or None) != (spec["parent_layer_id"] or None)
            or str(layer.get("artifact_kind") or "") != "chunks"
        ):
            if created:
                self._layers.delete_layer(layer_id)
            raise ValueError("codecompass_layer_upload_binding_invalid")
        return {"layer_id": layer_id, "created": created, "record_count": int(layer.get("record_count") or 0),
                "tombstone_count": int(layer.get("tombstone_count") or 0)}
