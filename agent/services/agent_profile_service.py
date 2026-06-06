"""AgentProfileService — APRL-003/004/005/006.

Loads profile-map.json, resolves the active AGENTS.md profile for a task,
composes Root-AGENTS.md + profile AGENTS.md and returns a structured result
with full diagnostics.  No Flask dependency; fully unit-testable.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROFILE_MAP_REL = "docs/agent-profiles/profile-map.json"
_ROOT_AGENTS_REL = "AGENTS.md"

_COMPOSED_SEPARATOR_GLOBAL = "# === Global AGENTS (Root) ==="
_COMPOSED_SEPARATOR_PROFILE = "# === Active Path Profile: {profile_id} ==="
_COMPOSED_SEPARATOR_CONSTRAINTS = "# === Runtime Workspace Constraints ==="

# Deterministic resolution order (APRL-004)
_RESOLUTION_ORDER = [
    "explicit_profile_id",
    "template_id",
    "agent_template",
    "task_kind",
    "mode",
    "keyword_fallback",
]

# task_kind normalisation aliases (keep bug_fix/code_fix separate — APRL-004)
_KIND_ALIASES: dict[str, str] = {
    "bug": "bug_fix",
    "bugfix": "bug_fix",
    "fix": "bug_fix",
    "codeproblem": "code_fix",
    "patch": "code_fix",
    "refactoring": "refactor",
    "cleanup": "refactor",
    "nsp": "new_software_project",
    "tdd_cycle": "tdd",
    "sysdiag": "sys_diag",
    "diagnosis": "sys_diag",
}


@dataclass
class AgentProfileResult:
    profile_id: str
    agents_file: str
    primary_role: str | None
    activation_source: str
    root_agents_content: str
    profile_agents_content: str | None
    composed_content: str
    checksums: dict[str, str]
    warnings: list[str]
    is_fallback: bool
    fallback_reason: str | None
    diagnostics: dict[str, Any]
    resolved_at: float = field(default_factory=time.time)

    def to_metadata(self) -> dict[str, Any]:
        """Compact metadata safe for manifests and JSON payloads (no full text)."""
        return {
            "profile_id": self.profile_id,
            "agents_file": self.agents_file,
            "primary_role": self.primary_role,
            "activation_source": self.activation_source,
            "is_fallback": self.is_fallback,
            "fallback_reason": self.fallback_reason,
            "checksums": dict(self.checksums),
            "warnings": list(self.warnings),
            "resolved_at": self.resolved_at,
        }


class AgentProfileService:
    """Resolves and composes agent profiles from profile-map.json for a given task."""

    def __init__(self, *, repo_root: Path | None = None) -> None:
        self._repo_root = repo_root or Path(__file__).resolve().parents[2]
        self._profile_map: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_for_task(self, task: dict[str, Any] | None) -> AgentProfileResult:
        """Resolve the active profile for *task* using the deterministic priority chain."""
        task = dict(task or {})
        profile_map = self._load_profile_map()
        profiles: dict[str, Any] = dict(profile_map.get("profiles") or {})

        profile_id, activation_source, warnings = self._resolve_profile_id(task, profiles)

        if profile_id and profile_id in profiles:
            profile_cfg = dict(profiles[profile_id])
            agents_file_rel = str(profile_cfg.get("agents_file") or "").strip()
            primary_role = str(profile_cfg.get("primary_role") or "").strip() or None
            return self._build_result(
                profile_id=profile_id,
                agents_file_rel=agents_file_rel,
                primary_role=primary_role,
                activation_source=activation_source,
                extra_warnings=warnings,
                is_fallback=False,
                fallback_reason=None,
            )

        fallback_reason = f"no_profile_matched_for_activation_source={activation_source}" if activation_source != "root_only" else "root_only"
        return self._build_result(
            profile_id="root_only",
            agents_file_rel=_ROOT_AGENTS_REL,
            primary_role=None,
            activation_source="root_only",
            extra_warnings=warnings + ([f"fallback_to_root: {fallback_reason}"] if activation_source != "root_only" else []),
            is_fallback=True,
            fallback_reason=fallback_reason,
        )

    def resolve_by_profile_id(self, profile_id: str) -> AgentProfileResult:
        """Directly resolve by an explicit profile_id string."""
        task = {"worker_execution_context": {"active_agent_profile_id": profile_id}}
        return self.resolve_for_task(task)

    def compose_content(self, result: AgentProfileResult, *, runtime_constraints: str | None = None) -> str:
        """Re-compose content with optional additional runtime constraints section."""
        parts = [
            _COMPOSED_SEPARATOR_GLOBAL,
            result.root_agents_content.strip(),
        ]
        if result.profile_agents_content and not result.is_fallback:
            parts += [
                "",
                _COMPOSED_SEPARATOR_PROFILE.format(profile_id=result.profile_id),
                result.profile_agents_content.strip(),
            ]
        if runtime_constraints:
            parts += [
                "",
                _COMPOSED_SEPARATOR_CONSTRAINTS,
                runtime_constraints.strip(),
            ]
        return "\n\n".join(parts).strip() + "\n"

    def profile_ids(self) -> list[str]:
        pm = self._load_profile_map()
        return list((pm.get("profiles") or {}).keys())

    # ------------------------------------------------------------------
    # Internal: profile-map loading
    # ------------------------------------------------------------------

    def _load_profile_map(self) -> dict[str, Any]:
        if self._profile_map is not None:
            return self._profile_map
        path = self._safe_resolve_path(_PROFILE_MAP_REL)
        if path is None or not path.exists():
            return {"profiles": {}}
        try:
            self._profile_map = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._profile_map = {"profiles": {}}
        return self._profile_map  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Internal: resolution (APRL-004)
    # ------------------------------------------------------------------

    def _resolve_profile_id(
        self, task: dict, profiles: dict
    ) -> tuple[str | None, str, list[str]]:
        warnings: list[str] = []

        # 1. Explicit profile_id beats everything
        wec = dict(task.get("worker_execution_context") or {})
        explicit = str(
            wec.get("active_agent_profile_id")
            or (dict(wec.get("instruction_context") or {})).get("agent_profile_id")
            or ""
        ).strip()
        if explicit:
            if explicit in profiles:
                return explicit, "explicit_profile_id", warnings
            warnings.append(f"explicit_profile_id={explicit!r} not in profile_map; falling back")

        # 2. template_id from worker_execution_context or agent_template field
        template_id = str(
            wec.get("template_id")
            or task.get("agent_template")
            or ""
        ).strip().lower()
        if template_id:
            matched = self._match_by_activation(template_id, profiles)
            if matched:
                return matched, "template_id", warnings

        # 3. task_kind (normalised)
        raw_kind = str(task.get("task_kind") or "").strip().lower()
        kind = _KIND_ALIASES.get(raw_kind, raw_kind)
        if kind:
            if kind in profiles:
                return kind, "task_kind", warnings
            matched = self._match_by_activation(kind, profiles)
            if matched:
                return matched, "task_kind", warnings

        # 4. mode / mode_data
        mode = str(task.get("mode") or "").strip().lower()
        if mode:
            if mode in profiles:
                return mode, "mode", warnings
            matched = self._match_by_activation(mode, profiles)
            if matched:
                return matched, "mode", warnings

        # 5. Keyword fallback from title+description — always marked as fallback
        title = str(task.get("title") or "").lower()
        desc = str(task.get("description") or "").lower()
        text = f"{title} {desc}".strip()
        if text:
            matched, ambiguous = self._match_by_keywords(text, profiles)
            if matched:
                if ambiguous:
                    warnings.append(f"keyword_fallback_ambiguous: multiple profiles matched; selected {matched!r}")
                return matched, "keyword_fallback", warnings

        return None, "root_only", warnings

    def _match_by_activation(self, value: str, profiles: dict) -> str | None:
        for pid, cfg in profiles.items():
            activations = [str(a).lower() for a in list(cfg.get("activation") or [])]
            if value in activations:
                return pid
        return None

    def _match_by_keywords(self, text: str, profiles: dict) -> tuple[str | None, bool]:
        matches: list[str] = []
        for pid, cfg in profiles.items():
            activations = [str(a).lower() for a in list(cfg.get("activation") or [])]
            if any(kw in text for kw in activations):
                matches.append(pid)
        if not matches:
            return None, False
        return matches[0], len(matches) > 1

    # ------------------------------------------------------------------
    # Internal: file loading + composition (APRL-005)
    # ------------------------------------------------------------------

    def _build_result(
        self,
        *,
        profile_id: str,
        agents_file_rel: str,
        primary_role: str | None,
        activation_source: str,
        extra_warnings: list[str],
        is_fallback: bool,
        fallback_reason: str | None,
    ) -> AgentProfileResult:
        warnings = list(extra_warnings)

        root_content = self._load_agents_file(_ROOT_AGENTS_REL)
        if not root_content:
            root_content = "# Root AGENTS.md not found"
            warnings.append("root_agents_file_missing")

        profile_content: str | None = None
        resolved_agents_file = agents_file_rel

        if not is_fallback and agents_file_rel and agents_file_rel != _ROOT_AGENTS_REL:
            safe_path = self._safe_resolve_path(agents_file_rel)
            if safe_path is None:
                warnings.append(f"agents_file_path_traversal_rejected: {agents_file_rel}")
                is_fallback = True
                fallback_reason = "path_traversal_rejected"
                resolved_agents_file = _ROOT_AGENTS_REL
            elif not safe_path.exists():
                warnings.append(f"agents_file_missing: {agents_file_rel}")
                is_fallback = True
                fallback_reason = f"agents_file_not_found:{agents_file_rel}"
                resolved_agents_file = _ROOT_AGENTS_REL
            else:
                profile_content = safe_path.read_text(encoding="utf-8")
                conflict = self._detect_root_conflict(root_content, profile_content)
                if conflict:
                    warnings.append(f"profile_conflicts_with_root: {conflict}")

        checksums: dict[str, str] = {
            "root": _sha256(root_content),
        }
        if profile_content:
            checksums["profile"] = _sha256(profile_content)

        composed = self._compose(
            root_content=root_content,
            profile_content=profile_content if not is_fallback else None,
            profile_id=profile_id,
        )

        diagnostics: dict[str, Any] = {
            "profile_id": profile_id,
            "agents_file": resolved_agents_file,
            "primary_role": primary_role,
            "activation_source": activation_source,
            "is_fallback": is_fallback,
            "fallback_reason": fallback_reason,
            "checksums": checksums,
            "warnings": warnings,
            "resolution_order": _RESOLUTION_ORDER,
        }

        return AgentProfileResult(
            profile_id=profile_id,
            agents_file=resolved_agents_file,
            primary_role=primary_role,
            activation_source=activation_source,
            root_agents_content=root_content,
            profile_agents_content=profile_content,
            composed_content=composed,
            checksums=checksums,
            warnings=warnings,
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
            diagnostics=diagnostics,
        )

    def _compose(
        self,
        *,
        root_content: str,
        profile_content: str | None,
        profile_id: str,
    ) -> str:
        parts = [
            _COMPOSED_SEPARATOR_GLOBAL,
            root_content.strip(),
        ]
        if profile_content:
            parts += [
                "",
                _COMPOSED_SEPARATOR_PROFILE.format(profile_id=profile_id),
                profile_content.strip(),
            ]
        return "\n\n".join(parts).strip() + "\n"

    def _detect_root_conflict(self, root_content: str, profile_content: str) -> str | None:
        """Heuristic: detect if profile tries to negate root governance rules."""
        _CONFLICT_PATTERNS = [
            re.compile(r"(ignore|bypass|override|disable)\s+(hub|governance|policy|guardrail|approval)", re.IGNORECASE),
            re.compile(r"workers?\s+(may|can|should)\s+orchestrate", re.IGNORECASE),
            re.compile(r"direct\s+worker.to.worker", re.IGNORECASE),
        ]
        for pat in _CONFLICT_PATTERNS:
            if pat.search(profile_content):
                return pat.pattern
        return None

    def _load_agents_file(self, rel_path: str) -> str:
        path = self._safe_resolve_path(rel_path)
        if path is None or not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _safe_resolve_path(self, rel_path: str) -> Path | None:
        """Resolve relative path under repo_root; reject path traversal."""
        if not rel_path:
            return None
        try:
            candidate = (self._repo_root / rel_path).resolve()
            if not str(candidate).startswith(str(self._repo_root)):
                return None
            return candidate
        except Exception:
            return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Module-level singleton
_agent_profile_service: AgentProfileService | None = None


def get_agent_profile_service() -> AgentProfileService:
    global _agent_profile_service
    if _agent_profile_service is None:
        _agent_profile_service = AgentProfileService()
    return _agent_profile_service
