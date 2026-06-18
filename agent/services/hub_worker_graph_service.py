"""Read model for the Hub/Worker orchestration graph editor.

This service exposes configured hub/worker relationships for UI inspection.
It does not dispatch work and does not create worker-to-worker orchestration.
The hub remains the only control-plane node.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HubWorkerNode:
    id: str
    node_type: str
    label: str
    runtime_active: bool
    source_file: str | None = "user.json"
    source_kind: str | None = "user_config"
    source_pointer: str | None = None
    writable: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "label": self.label,
            "runtime_active": self.runtime_active,
            "source_file": self.source_file,
            "source_kind": self.source_kind,
            "source_pointer": self.source_pointer,
            "writable": self.writable,
            "data": self.data,
            "diagnostics": self.diagnostics,
        }


@dataclass
class HubWorkerEdge:
    source: str
    target: str
    edge_type: str
    label: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type,
            "label": self.label,
            "data": self.data,
        }


class HubWorkerGraphService:
    """Builds a hub-centered read model from the active Ananta config."""

    _KNOWN_WORKERS = ("ananta-worker", "opencode", "hermes")

    def build(
        self,
        *,
        user_config: dict[str, Any],
        path: str | None = None,
    ) -> dict[str, Any]:
        cfg = dict(user_config or {})
        nodes: dict[str, HubWorkerNode] = {}
        edges: list[HubWorkerEdge] = []
        diagnostics: list[str] = []

        hub = HubWorkerNode(
            id="hub::ananta",
            node_type="hub",
            label="Ananta Hub",
            runtime_active=True,
            source_pointer="/",
            writable=False,
            data={
                "path": path or "",
                "chat_backend": cfg.get("chat_backend") or cfg.get("backend") or "",
                "governance_mode": cfg.get("governance_mode") or "",
                "worker_runtime_enabled": (
                    bool((cfg.get("worker_runtime") or {}).get("enabled", True))
                    if isinstance(cfg.get("worker_runtime"), dict)
                    else True
                ),
            },
        )
        nodes[hub.id] = hub

        for worker_id in self._configured_workers(cfg):
            node = self._worker_node(worker_id, cfg)
            nodes[node.id] = node
            edges.append(HubWorkerEdge(
                source=hub.id,
                target=node.id,
                edge_type="controls_worker",
                label="Hub steuert",
                data={"control_plane": "hub"},
            ))

        for task_kind in self._task_kinds(cfg):
            task_node = HubWorkerNode(
                id=f"task_kind::{task_kind}",
                node_type="task_kind",
                label=task_kind,
                runtime_active=True,
                source_pointer="/docs/agent-profiles/profile-map.json",
                writable=False,
                data={"task_kind": task_kind},
            )
            nodes[task_node.id] = task_node
            preferred = self._preferred_worker_for_task(task_kind, cfg)
            target = f"worker_instance::{preferred}"
            if target in nodes:
                edges.append(HubWorkerEdge(
                    source=hub.id,
                    target=target,
                    edge_type="routes_task_to_worker",
                    label=f"{task_kind} -> {preferred}",
                    data={"task_kind": task_kind, "preferred_worker": preferred},
                ))
                edges.append(HubWorkerEdge(
                    source=task_node.id,
                    target=target,
                    edge_type="routes_task_to_worker",
                    label=preferred,
                    data={"task_kind": task_kind},
                ))
            else:
                diagnostics.append(
                    f"Routing for task_kind={task_kind} targets missing worker "
                    f"{preferred}"
                )

        self._add_fallback_chain(cfg, nodes, edges)
        self._add_taskflows(cfg, nodes, edges, diagnostics)

        if not any(node.node_type == "worker_instance" for node in nodes.values()):
            diagnostics.append(
                "No configured workers discovered; hub-only graph returned"
            )

        return {
            "schema": "ananta.hub_worker_graph.v1",
            "path": path or "",
            "nodes": {key: node.to_dict() for key, node in nodes.items()},
            "edges": [edge.to_dict() for edge in edges],
            "diagnostics": diagnostics,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def _configured_workers(self, cfg: dict[str, Any]) -> list[str]:
        found: list[str] = []
        chat_backend = self._normalize_worker_id(
            cfg.get("chat_backend") or cfg.get("backend")
        )
        if chat_backend:
            found.append(chat_backend)
        if isinstance(cfg.get("worker_runtime"), dict):
            found.append("ananta-worker")
        if isinstance(cfg.get("opencode_runtime"), dict):
            found.append("opencode")
        hermes_cfg = cfg.get("hermes_worker_adapter")
        if isinstance(hermes_cfg, dict) and bool(hermes_cfg.get("enabled", False)):
            found.append("hermes")
        specialized = cfg.get("specialized_worker_profiles")
        if isinstance(specialized, dict):
            for profile in (specialized.get("profiles") or {}).values():
                if isinstance(profile, dict):
                    backend_id = self._normalize_worker_id(
                        profile.get("backend_id") or profile.get("backend_type")
                    )
                    if backend_id:
                        found.append(backend_id)
        deduped: list[str] = []
        for item in found or ["ananta-worker"]:
            normalized = self._normalize_worker_id(item)
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped

    def _worker_node(self, worker_id: str, cfg: dict[str, Any]) -> HubWorkerNode:
        source_pointer = {
            "ananta-worker": "/worker_runtime",
            "opencode": "/opencode_runtime",
            "hermes": "/hermes_worker_adapter",
        }.get(worker_id)
        block = self._worker_config_block(worker_id, cfg)
        diagnostics: list[str] = []
        if worker_id == "opencode" and not isinstance(
            cfg.get("opencode_runtime"), dict
        ):
            diagnostics.append(
                "opencode_runtime not configured; "
                "worker is inferred from backend selection"
            )
        if worker_id == "hermes" and not block.get("enabled", False):
            diagnostics.append("hermes adapter is not enabled")
        return HubWorkerNode(
            id=f"worker_instance::{worker_id}",
            node_type="worker_instance",
            label=worker_id,
            runtime_active=worker_id != "hermes" or bool(block.get("enabled", False)),
            source_pointer=source_pointer,
            writable=bool(source_pointer),
            data={
                "worker_id": worker_id,
                "adapter": worker_id,
                "config": block,
                "capabilities": self._worker_capabilities(worker_id),
            },
            diagnostics=diagnostics,
        )

    @staticmethod
    def _worker_config_block(worker_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
        key = {
            "ananta-worker": "worker_runtime",
            "opencode": "opencode_runtime",
            "hermes": "hermes_worker_adapter",
        }.get(worker_id, "")
        block = cfg.get(key)
        return dict(block) if isinstance(block, dict) else {}

    @staticmethod
    def _worker_capabilities(worker_id: str) -> list[str]:
        if worker_id == "opencode":
            return ["coding", "analysis", "doc", "ops"]
        if worker_id == "hermes":
            return ["review", "planning"]
        return ["planning", "routing", "coding", "analysis", "doc", "ops"]

    @staticmethod
    def _task_kinds(cfg: dict[str, Any]) -> list[str]:
        configured = cfg.get("hub_worker_task_kinds")
        if isinstance(configured, list):
            values = [
                str(item or "").strip()
                for item in configured
                if str(item or "").strip()
            ]
            if values:
                return sorted(set(values))
        return [
            "repair",
            "new_software_project",
            "review",
            "refactor",
            "documentation",
        ]

    @staticmethod
    def _preferred_worker_for_task(task_kind: str, cfg: dict[str, Any]) -> str:
        routing = cfg.get("hub_worker_routing")
        if isinstance(routing, dict):
            worker = HubWorkerGraphService._normalize_worker_id(routing.get(task_kind))
            if worker:
                return worker
        if task_kind in {"repair", "refactor", "new_software_project"}:
            if isinstance(cfg.get("opencode_runtime"), dict):
                return "opencode"
            return "ananta-worker"
        if task_kind == "review" and isinstance(
            cfg.get("hermes_worker_adapter"), dict
        ):
            return "hermes"
        return "ananta-worker"

    def _add_fallback_chain(
        self,
        cfg: dict[str, Any],
        nodes: dict[str, HubWorkerNode],
        edges: list[HubWorkerEdge],
    ) -> None:
        policy = cfg.get("routing_fallback_policy")
        fallback_order = []
        if isinstance(policy, dict) and isinstance(policy.get("fallback_order"), list):
            fallback_order = [
                self._normalize_worker_id(item)
                for item in policy.get("fallback_order") or []
                if self._normalize_worker_id(item)
            ]
        if not fallback_order:
            fallback_order = [
                worker_id.split("::", 1)[1]
                for worker_id, node in nodes.items()
                if node.node_type == "worker_instance"
            ]
        if len(fallback_order) < 2:
            return
        chain_id = "fallback_chain::worker-routing"
        nodes[chain_id] = HubWorkerNode(
            id=chain_id,
            node_type="fallback_chain",
            label="Worker-Fallback",
            runtime_active=True,
            source_pointer="/routing_fallback_policy",
            writable=False,
            data={"fallback_order": fallback_order},
        )
        for index, worker_id in enumerate(fallback_order):
            target = f"worker_instance::{worker_id}"
            if target in nodes:
                edges.append(HubWorkerEdge(
                    source=chain_id,
                    target=target,
                    edge_type="falls_back_to",
                    label=str(index + 1),
                    data={"order": index + 1},
                ))

    def _add_taskflows(
        self,
        cfg: dict[str, Any],
        nodes: dict[str, HubWorkerNode],
        edges: list[HubWorkerEdge],
        diagnostics: list[str],
    ) -> None:
        raw_flows = cfg.get("hub_worker_taskflows")
        flows = raw_flows if isinstance(raw_flows, dict) else {
            "repair": ["Planner", "Developer", "Reviewer", "Verifier"],
            "new_software_project": ["Planner", "Developer", "Reviewer"],
        }
        for flow_id, raw_steps in flows.items():
            if not isinstance(raw_steps, list):
                diagnostics.append(f"Taskflow {flow_id} steps must be a list")
                continue
            taskflow_id = f"taskflow::{flow_id}"
            nodes[taskflow_id] = HubWorkerNode(
                id=taskflow_id,
                node_type="taskflow",
                label=str(flow_id),
                runtime_active=True,
                source_pointer="/hub_worker_taskflows",
                writable=False,
                data={"taskflow_id": flow_id},
            )
            previous_step_id = ""
            for index, raw_step in enumerate(raw_steps):
                if isinstance(raw_step, dict):
                    step_name = str(raw_step.get("name") or f"step_{index + 1}")
                    worker = self._normalize_worker_id(raw_step.get("worker"))
                else:
                    step_name = str(raw_step or f"step_{index + 1}")
                    worker = self._preferred_worker_for_task(str(flow_id), cfg)
                step_id = f"taskflow_step::{flow_id}::{index + 1}"
                nodes[step_id] = HubWorkerNode(
                    id=step_id,
                    node_type="taskflow_step",
                    label=step_name,
                    runtime_active=True,
                    source_pointer=f"/hub_worker_taskflows/{flow_id}/{index}",
                    writable=False,
                    data={
                        "taskflow_id": flow_id,
                        "index": index + 1,
                        "worker": worker,
                    },
                )
                edges.append(HubWorkerEdge(
                    source=taskflow_id,
                    target=step_id,
                    edge_type="executes_step",
                    label=str(index + 1),
                ))
                if previous_step_id:
                    edges.append(HubWorkerEdge(
                        source=previous_step_id,
                        target=step_id,
                        edge_type="hands_off_artifact_to",
                        label="handoff",
                    ))
                previous_step_id = step_id
                worker_target = f"worker_instance::{worker}"
                if worker and worker_target in nodes:
                    edges.append(HubWorkerEdge(
                        source=step_id,
                        target=worker_target,
                        edge_type="routes_task_to_worker",
                        label=worker,
                        data={"taskflow_id": flow_id, "step": step_name},
                    ))

    @staticmethod
    def _normalize_worker_id(value: Any) -> str:
        raw = str(value or "").strip().lower().replace("_", "-")
        aliases = {
            "ananta": "ananta-worker",
            "worker": "ananta-worker",
            "ananta-worker": "ananta-worker",
            "sgpt": "ananta-worker",
            "open-code": "opencode",
            "opencode": "opencode",
            "hermes": "hermes",
        }
        if raw in HubWorkerGraphService._KNOWN_WORKERS:
            return raw
        return aliases.get(raw, "")
