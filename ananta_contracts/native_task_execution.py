"""Closed Native execution projection; no task history, credentials or queue ownership."""

import math
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.native_result_maintenance import require_maintenance_recipient

NATIVE_TASK_EXECUTION_SCHEMA = "ananta.native-task-execution.v1"
MAX_NATIVE_TASK_HTTP_BYTES = 262144
_REQUIRED = frozenset({"id", "tenant_id", "task_kind", "derivation_reason", "worker_execution_context"})
_FIELDS = _REQUIRED | {"project_id", "input_files", "context_bundle_id"}


@dataclass(frozen=True)
class VerifiedNativeExecution:
    task: dict
    evidence_assignment: dict | None


def _evidence_projection(value, task):
    projection = validate_hub_evidence_assignment(value)
    if task["task_kind"] != "pi_coding_agent" or projection["task_id"] != task["id"]:
        raise ValueError("native_execution_evidence_task_mismatch")
    return deepcopy(projection)


def sign_native_execution_request(task, *, key_ring, worker_id, worker_url, expires_at, evidence_assignment=None):
    if key_ring.signature_algorithm != "ed25519":
        raise ValueError("native_execution_public_verification_required")
    payload = {**native_execution_request(task), "worker_id": worker_id,
               "worker_url": worker_url, "expires_at": expires_at}
    require_maintenance_recipient(payload['task'], worker_id=worker_id, worker_url=worker_url)
    if evidence_assignment is not None:
        payload["evidence_assignment"] = _evidence_projection(evidence_assignment, payload["task"])
    key_id, signature = key_ring.sign(namespace=NATIVE_TASK_EXECUTION_SCHEMA, payload=payload)
    return {**payload, "key_id": key_id, "signature": signature}


def verify_native_execution_request(value, *, key_ring, worker_id, worker_url, now=None):
    verified = verify_native_execution_envelope(
        value, key_ring=key_ring, worker_id=worker_id, worker_url=worker_url, now=now,
    )
    if verified.evidence_assignment is not None:
        raise ValueError("native_execution_evidence_consumer_required")
    return verified.task


def verify_native_execution_envelope(value, *, key_ring, worker_id, worker_url, now=None):
    fields = {"schema", "task", "worker_id", "worker_url", "expires_at", "key_id", "signature"}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {"evidence_assignment"}):
        raise ValueError("native_execution_signed_request_required")
    payload = {name: value[name] for name in set(value) - {"key_id", "signature"}}
    projected = parse_native_execution_request({"schema": payload["schema"], "task": payload["task"]})
    evidence = (_evidence_projection(payload["evidence_assignment"], projected["task"])
                if "evidence_assignment" in payload else None)
    expiry = payload["expires_at"]
    clock = time.time() if now is None else now
    if (type(clock) not in (int, float) or not math.isfinite(clock)
            or type(expiry) not in (int, float) or not math.isfinite(expiry)
            or not clock < expiry <= clock + 60):
        raise ValueError("native_execution_expired")
    if not worker_id or not worker_url or (payload["worker_id"], payload["worker_url"]) != (worker_id, worker_url):
        raise ValueError("native_execution_recipient_mismatch")
    if key_ring is None or key_ring.signature_algorithm != "ed25519":
        raise ValueError("native_execution_public_verification_required")
    key_ring.verify(namespace=NATIVE_TASK_EXECUTION_SCHEMA, payload=payload, key_id=value["key_id"],
                    signature=value["signature"], contract_id=projected["task"]["worker_execution_context"]
                    ["native_node_command"].get("command_id", ""))
    require_maintenance_recipient(projected['task'], worker_id=worker_id, worker_url=worker_url)
    return VerifiedNativeExecution(projected["task"], evidence)


def native_execution_request(task: Mapping) -> dict:
    if not isinstance(task, Mapping):
        raise ValueError("native_execution_task_invalid")
    return parse_native_execution_request({
        "schema": NATIVE_TASK_EXECUTION_SCHEMA,
        "task": {name: deepcopy(task[name]) for name in _FIELDS if name in task
                 and (name != "context_bundle_id" or task[name] is not None)},
    })


def parse_native_execution_request(value) -> dict:
    if (not isinstance(value, dict) or set(value) != {"schema", "task"}
            or value["schema"] != NATIVE_TASK_EXECUTION_SCHEMA):
        raise ValueError("native_execution_request_invalid")
    task = value["task"]
    if not isinstance(task, dict) or not _REQUIRED <= task.keys() or task.keys() - _FIELDS:
        raise ValueError("native_execution_task_invalid")
    if any(not isinstance(task[name], str) or not task[name] or len(task[name]) > 200
           for name in ("id", "tenant_id", "task_kind")):
        raise ValueError("native_execution_task_invalid")
    context = task["worker_execution_context"]
    if (task["derivation_reason"] != "native_graph_hub_delegation" or not isinstance(context, dict)
            or context.get("schema") != "ananta.native_graph_worker_context.v1"
            or context.get("runtime_path") != "native_graph_node"
            or not isinstance(context.get("native_node_command"), dict)):
        raise ValueError("native_execution_context_invalid")
    command = context["native_node_command"]
    node = command.get("node")
    if (command.get("tenant_id") != task["tenant_id"] or not isinstance(node, dict)
            or node.get("task_kind") != task["task_kind"]):
        raise ValueError("native_execution_binding_mismatch")
    if task.get("project_id") is not None and not isinstance(task["project_id"], str):
        raise ValueError("native_execution_task_invalid")
    if task.get("input_files") is not None and not isinstance(task["input_files"], list):
        raise ValueError("native_execution_task_invalid")
    if "context_bundle_id" in task:
        reference = task["context_bundle_id"]
        if (not isinstance(reference, str) or not 0 < len(reference) <= 256
                or any(ord(char) < 33 for char in reference)):
            raise ValueError("native_execution_context_reference_invalid")
    return deepcopy(value)
