"""Persistence adapters for VACGE graph patches.

The patch service mutates graph snapshots.  This service maps supported graph
operations back to concrete configuration artifacts and returns source diffs and
rollback metadata.  Unsupported or runtime-only nodes stay readonly.
"""
from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.services.config_graph_builder_service import (
    NODE_AGENT_PROFILE,
    NODE_EMBEDDING_MODEL,
    NODE_MODEL_PROVIDER,
    NODE_PATH_RULE,
    ConfigGraph,
)
from agent.services.config_graph_patch_service import PatchOp


@dataclass
class SourceDiff:
    source_file: str
    source_kind: str
    diff: str
    rollback_content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_kind": self.source_kind,
            "diff": self.diff,
            "rollback_content": self.rollback_content,
        }


@dataclass
class PersistenceResult:
    success: bool = True
    source_diffs: list[SourceDiff] = field(default_factory=list)
    rollback_artifact: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "source_diffs": [item.to_dict() for item in self.source_diffs],
            "rollback_artifact": self.rollback_artifact,
            "errors": self.errors,
        }


class ConfigGraphPersistenceService:
    """Applies supported graph patch ops to durable config sources."""

    def __init__(self, *, repo_root: str | Path | None = None) -> None:
        self._root = Path(repo_root or Path(__file__).parents[2]).resolve()

    def persist(self, graph: ConfigGraph, ops: list[PatchOp]) -> PersistenceResult:
        result = PersistenceResult(
            rollback_artifact={
                "schema": "ananta.config_graph.rollback.v1",
                "created_at": time.time(),
                "sources": [],
            }
        )
        staged: dict[Path, tuple[str, str, str]] = {}

        try:
            for op in ops:
                self._stage_op(graph, op, staged)
        except ValueError as exc:
            result.success = False
            result.errors.append(str(exc))
            return result

        for path, (before, after, source_kind) in staged.items():
            if before == after:
                continue
            rel = self._relative(path)
            diff_text = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            ))
            result.source_diffs.append(SourceDiff(
                source_file=rel,
                source_kind=source_kind,
                diff=diff_text,
                rollback_content=before,
            ))
            result.rollback_artifact["sources"].append({
                "source_file": rel,
                "source_kind": source_kind,
                "rollback_content": before,
            })

        try:
            for path, (_before, after, _source_kind) in staged.items():
                path.write_text(after, encoding="utf-8")
        except Exception as exc:
            result.success = False
            result.errors.append(f"write_failed:{self._relative(path)}:{exc}")
            return result

        return result

    def _stage_op(
        self,
        graph: ConfigGraph,
        op: PatchOp,
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        if op.op == "set_data":
            self._stage_set_data(graph, op, staged)
            return
        if op.op == "add_node":
            self._stage_add_node(op, staged)
            return
        raise ValueError(
            f"op {op.op!r} is not persistable; "
            "structural runtime graph edits are readonly"
        )

    def _stage_set_data(
        self,
        graph: ConfigGraph,
        op: PatchOp,
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        node = graph.nodes.get(op.target)
        if node is None:
            raise ValueError(f"node not found: {op.target}")
        if not node.writable:
            raise ValueError(f"node is readonly: {op.target}")
        if node.node_type == NODE_AGENT_PROFILE:
            self._stage_profile_update(node.data.get("profile_id"), op.data, staged)
            return
        if node.node_type == NODE_PATH_RULE:
            self._stage_path_rule_update(node.data.get("path_glob"), op.data, staged)
            return
        if node.node_type == NODE_MODEL_PROVIDER:
            self._stage_model_provider_update(op.data, staged)
            return
        if node.node_type == NODE_EMBEDDING_MODEL:
            self._stage_user_config_block_update(
                "embedding_provider",
                op.data,
                staged,
            )
            return
        raise ValueError(f"node type is not persistable: {node.node_type}")

    def _stage_add_node(
        self,
        op: PatchOp,
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        node_type = str(op.data.get("node_type") or "")
        data = dict(op.data.get("data") or {})
        if node_type == NODE_AGENT_PROFILE:
            self._stage_profile_create(data, staged)
            return
        if node_type == NODE_PATH_RULE:
            self._stage_path_rule_create(data, staged)
            return
        raise ValueError(f"node type is not persistable for add_node: {node_type}")

    def _stage_profile_update(
        self,
        profile_id_raw: Any,
        updates: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        profile_id = str(profile_id_raw or "").strip()
        if not profile_id:
            raise ValueError("agent profile update requires profile_id")
        profile_map = self._load_json_staged(
            self._root / "docs/agent-profiles/profile-map.json",
            "profile_map",
            staged,
        )
        profiles = profile_map.setdefault("profiles", {})
        if profile_id not in profiles or not isinstance(profiles[profile_id], dict):
            raise ValueError(f"profile not found in profile-map.json: {profile_id}")
        profiles[profile_id].update(self._clean_profile_payload(updates))
        self._store_json_staged(
            self._root / "docs/agent-profiles/profile-map.json",
            profile_map,
            "profile_map",
            staged,
        )

    def _stage_profile_create(
        self,
        data: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        profile_id = str(data.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError("profile_id is required")
        if not profile_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("profile_id may only contain letters, digits, _ and -")
        profile_map = self._load_json_staged(
            self._root / "docs/agent-profiles/profile-map.json",
            "profile_map",
            staged,
        )
        profiles = profile_map.setdefault("profiles", {})
        if profile_id in profiles:
            raise ValueError(f"profile already exists: {profile_id}")
        profiles[profile_id] = self._clean_profile_payload(data)
        self._store_json_staged(
            self._root / "docs/agent-profiles/profile-map.json",
            profile_map,
            "profile_map",
            staged,
        )

    def _stage_path_rule_update(
        self,
        glob_raw: Any,
        updates: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        glob = str(glob_raw or "").strip()
        if not glob:
            raise ValueError("path rule update requires path_glob")
        config = self._load_json_staged(self._root / "user.json", "user_config", staged)
        rules = list(config.get("path_ai_modes") or [])
        for index, rule in enumerate(rules):
            if isinstance(rule, dict) and str(rule.get("path_glob") or "") == glob:
                rule.update(self._clean_path_rule_payload(updates))
                rules[index] = rule
                config["path_ai_modes"] = rules
                self._store_json_staged(
                    self._root / "user.json",
                    config,
                    "user_config",
                    staged,
                )
                return
        raise ValueError(f"path rule not found in user.json: {glob}")

    def _stage_path_rule_create(
        self,
        data: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        glob = str(data.get("path_glob") or "").strip()
        if not glob:
            raise ValueError("path_glob is required")
        config = self._load_json_staged(self._root / "user.json", "user_config", staged)
        rules = list(config.get("path_ai_modes") or [])
        if any(
            isinstance(rule, dict) and str(rule.get("path_glob") or "") == glob
            for rule in rules
        ):
            raise ValueError(f"path rule already exists: {glob}")
        rules.append(self._clean_path_rule_payload(data))
        config["path_ai_modes"] = rules
        self._store_json_staged(
            self._root / "user.json",
            config,
            "user_config",
            staged,
        )

    def persist_user_config_block(
        self,
        *,
        key: str,
        data: dict[str, Any],
    ) -> PersistenceResult:
        result = PersistenceResult(
            rollback_artifact={
                "schema": "ananta.config_graph.rollback.v1",
                "created_at": time.time(),
                "sources": [],
            }
        )
        staged: dict[Path, tuple[str, str, str]] = {}
        try:
            self._stage_user_config_block_update(key, data, staged)
        except ValueError as exc:
            result.success = False
            result.errors.append(str(exc))
            return result

        for path, (before, after, source_kind) in staged.items():
            if before == after:
                continue
            rel = self._relative(path)
            diff_text = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            ))
            result.source_diffs.append(SourceDiff(
                source_file=rel,
                source_kind=source_kind,
                diff=diff_text,
                rollback_content=before,
            ))
            result.rollback_artifact["sources"].append({
                "source_file": rel,
                "source_kind": source_kind,
                "rollback_content": before,
            })
        try:
            for path, (_before, after, _source_kind) in staged.items():
                path.write_text(after, encoding="utf-8")
        except Exception as exc:
            result.success = False
            result.errors.append(f"write_failed:{self._relative(path)}:{exc}")
        return result

    def rollback(self, rollback_artifact: dict[str, Any]) -> PersistenceResult:
        result = PersistenceResult(success=True, rollback_artifact=rollback_artifact)
        sources = rollback_artifact.get("sources")
        if not isinstance(sources, list) or not sources:
            result.success = False
            result.errors.append("rollback_artifact.sources is required")
            return result
        for source in sources:
            if not isinstance(source, dict):
                result.success = False
                result.errors.append("rollback source must be an object")
                continue
            source_file = str(source.get("source_file") or "").strip()
            rollback_content = source.get("rollback_content")
            if not source_file or not isinstance(rollback_content, str):
                result.success = False
                result.errors.append(
                    "rollback source_file and rollback_content are required"
                )
                continue
            path = (self._root / source_file).resolve()
            try:
                path.relative_to(self._root)
            except ValueError:
                result.success = False
                result.errors.append(f"rollback path escapes repo root: {source_file}")
                continue
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            if before != rollback_content:
                rel = self._relative(path)
                result.source_diffs.append(SourceDiff(
                    source_file=rel,
                    source_kind=str(source.get("source_kind") or "unknown"),
                    diff="".join(difflib.unified_diff(
                        before.splitlines(keepends=True),
                        rollback_content.splitlines(keepends=True),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )),
                    rollback_content=before,
                ))
                path.write_text(rollback_content, encoding="utf-8")
        return result

    def _stage_model_provider_update(
        self,
        updates: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        backend = str(updates.get("backend") or "").strip()
        if not backend:
            raise ValueError("model_provider update requires backend")
        config = self._load_json_staged(self._root / "user.json", "user_config", staged)
        config["chat_backend"] = backend
        self._store_json_staged(
            self._root / "user.json",
            config,
            "user_config",
            staged,
        )

    def _stage_user_config_block_update(
        self,
        key: str,
        updates: dict[str, Any],
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        if key not in {
            "embedding_provider",
            "worker_runtime",
            "opencode_runtime",
            "hermes_worker_adapter",
            "hub_worker_routing",
        }:
            raise ValueError(f"user config block is not writable: {key}")
        config = self._load_json_staged(self._root / "user.json", "user_config", staged)
        current = config.get(key)
        if isinstance(current, dict):
            merged = dict(current)
            merged.update(updates)
            config[key] = merged
        else:
            config[key] = dict(updates)
        self._store_json_staged(
            self._root / "user.json",
            config,
            "user_config",
            staged,
        )

    @staticmethod
    def _clean_profile_payload(data: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "agents_file",
            "primary_role",
            "activation",
            "allowed_task_kinds",
            "code_change_policy",
            "context_policy_hint",
        }
        cleaned = {key: data[key] for key in allowed if key in data}
        cleaned.setdefault("code_change_policy", "via_hub_task_worker")
        cleaned.setdefault("activation", [])
        cleaned.setdefault("allowed_task_kinds", [])
        return cleaned

    @staticmethod
    def _clean_path_rule_payload(data: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "path_glob",
            "blocked_ai_modes",
            "allowed_ai_modes",
            "allow_free_text_generation",
            "allow_code_generation",
            "llm_scope",
            "max_input_chars",
        }
        return {key: data[key] for key in allowed if key in data}

    def _load_json_staged(
        self,
        path: Path,
        source_kind: str,
        staged: dict[Path, tuple[str, str, str]],
    ) -> dict[str, Any]:
        if path in staged:
            text = staged[path][1]
        else:
            if not path.exists():
                raise ValueError(f"source file not found: {self._relative(path)}")
            text = path.read_text(encoding="utf-8")
            staged[path] = (text, text, source_kind)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            rel = self._relative(path)
            raise ValueError(f"source JSON parse failed:{rel}:{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"source JSON must be an object: {self._relative(path)}")
        return payload

    def _store_json_staged(
        self,
        path: Path,
        payload: dict[str, Any],
        source_kind: str,
        staged: dict[Path, tuple[str, str, str]],
    ) -> None:
        before = staged[path][0] if path in staged else path.read_text(encoding="utf-8")
        after = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        staged[path] = (before, after, source_kind)

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self._root))
        except ValueError:
            return str(path)
