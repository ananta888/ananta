"""Pattern execution-context resolver.

Glue module that connects the three PAT layers:

- Plan side: ``PatternTemplateRenderer`` (deterministic render)
- Policy side: ``PatternSelectionPolicy`` (default-deny for risky)
- Worker side: ``PatternProposalNormalizer`` (proposal validation)

The resolver is the single entry point a hub component (e.g. the
task-scoped-execution service or a workflow gate) can call to turn
a normalized pattern plan into:

- a normalized ``PatternExecutionContext`` dict that fits into the
  existing ``worker_execution_context`` (additive field)
- a rendered file manifest (when target_root is supplied)
- a stable ``context_hash`` for audit and dedup

The resolver is read-only with respect to the registry and never
writes a database record itself. Persisting the result is the
caller's job (and is covered by the artifact service PAT-016).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from agent.services.pattern_proposal_normalizer import (
    PatternProposal,
    PatternProposalNormalizer,
    get_pattern_proposal_normalizer,
)
from agent.services.pattern_registry import get_registry
from agent.services.pattern_selection_policy import (
    PatternSelectionPolicy,
    get_pattern_selection_policy,
)
from agent.services.pattern_template_renderer import (
    PatternTemplateRenderer,
    RenderManifest,
    TemplateFile,
)


@dataclass(frozen=True)
class PatternExecutionContext:
    """Resolved context suitable for ``worker_execution_context``."""

    accepted: bool
    context_hash: str
    pattern_proposal: dict[str, Any] = field(default_factory=dict)
    render_manifest: Optional[dict[str, Any]] = None
    manifest_sha256: Optional[str] = None
    blocked_reason: Optional[str] = None
    risk_level: str = "low"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "context_hash": self.context_hash,
            "pattern_proposal": dict(self.pattern_proposal),
            "render_manifest": dict(self.render_manifest) if self.render_manifest is not None else None,
            "manifest_sha256": self.manifest_sha256,
            "blocked_reason": self.blocked_reason,
            "risk_level": self.risk_level,
            "warnings": list(self.warnings),
        }


class PatternExecutionContextResolver:
    """Resolves a (raw proposal, templates) pair into a
    ``PatternExecutionContext``.

    The resolver is intentionally stateless. Tests may inject a
    custom policy or normalizer; the default singletons are used
    otherwise.
    """

    def __init__(
        self,
        *,
        policy: Optional[PatternSelectionPolicy] = None,
        normalizer: Optional[PatternProposalNormalizer] = None,
        renderer: Optional[PatternTemplateRenderer] = None,
    ) -> None:
        self._policy = policy or get_pattern_selection_policy()
        self._normalizer = normalizer or get_pattern_proposal_normalizer()
        self._renderer = renderer or PatternTemplateRenderer()
        self._registry = get_registry()

    # --- public surface -----------------------------------------------

    def resolve(
        self,
        *,
        raw_proposal: dict[str, Any] | None,
        templates: Iterable[TemplateFile] | None = None,
        target_root: str | None = None,
    ) -> PatternExecutionContext:
        """Resolve a raw proposal into a PatternExecutionContext.

        Args:
            raw_proposal: the raw ``pattern_plan`` from worker
                metadata, or None.
            templates: optional iterable of TemplateFile for
                deterministic rendering. When None, the resolver
                does NOT render (it only normalizes + audits).
            target_root: optional directory to write rendered
                files to. When set, the resolver actually calls
                the renderer with on-disk output.

        Returns:
            A :class:`PatternExecutionContext` with stable
            ``context_hash`` for audit/dedup.
        """
        catalogue_ids: set[str] = set()
        for entry in self._registry.list():
            if isinstance(entry, dict) and isinstance(entry.get("pattern_id"), str):
                catalogue_ids.add(entry["pattern_id"])
        proposal: PatternProposal = self._normalizer.normalize(
            proposal=raw_proposal, catalogue_ids=catalogue_ids
        )

        warnings: list[str] = []
        manifest_dict: Optional[dict[str, Any]] = None
        manifest_sha: Optional[str] = None

        if not proposal.accepted:
            return self._finalize(
                proposal=proposal,
                manifest_dict=None,
                manifest_sha=None,
                warnings=warnings,
            )

        if templates is not None:
            try:
                manifest: RenderManifest = self._renderer.render(
                    pattern_plan={
                        "pattern_id": proposal.pattern_id,
                        "language": proposal.language,
                        "parameters": proposal.parameters_provided,
                    },
                    templates=list(templates),
                    target_root=target_root,
                )
                manifest_dict = manifest.to_dict()
                manifest_sha = manifest.manifest_sha256
                warnings.extend(manifest.warnings)
            except Exception as exc:  # RenderError or any I/O issue
                # Render failure is a hard failure: the proposal was
                # accepted, but we cannot honour it. Mark accepted=False
                # so callers don't accidentally proceed with an empty
                # manifest.
                blocked_proposal = PatternProposal(
                    accepted=False,
                    pattern_id=proposal.pattern_id,
                    task_kind=proposal.task_kind,
                    language=proposal.language,
                    parameters_provided=proposal.parameters_provided,
                    blocked_reason=f"render failed: {exc}",
                    risk_level=proposal.risk_level,
                    audit=proposal.audit,
                )
                return self._finalize(
                    proposal=blocked_proposal,
                    manifest_dict=None,
                    manifest_sha=None,
                    warnings=warnings,
                )

        return self._finalize(
            proposal=proposal,
            manifest_dict=manifest_dict,
            manifest_sha=manifest_sha,
            warnings=warnings,
        )

    # --- internals -----------------------------------------------------

    def _finalize(
        self,
        *,
        proposal: PatternProposal,
        manifest_dict: Optional[dict[str, Any]],
        manifest_sha: Optional[str],
        warnings: list[str],
    ) -> PatternExecutionContext:
        payload = {
            "accepted": proposal.accepted,
            "pattern_id": proposal.pattern_id,
            "task_kind": proposal.task_kind,
            "language": proposal.language,
            "parameters_provided": proposal.parameters_provided,
            "blocked_reason": proposal.blocked_reason,
            "risk_level": proposal.risk_level,
            "manifest_sha256": manifest_sha,
        }
        # Stable hash: sort_keys gives byte-identical input -> output.
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        context_hash = hashlib.sha256(encoded).hexdigest()
        return PatternExecutionContext(
            accepted=proposal.accepted,
            context_hash=context_hash,
            pattern_proposal=proposal.to_metadata(),
            render_manifest=manifest_dict,
            manifest_sha256=manifest_sha,
            blocked_reason=payload["blocked_reason"],
            risk_level=proposal.risk_level,
            warnings=warnings,
        )


_default_resolver: Optional[PatternExecutionContextResolver] = None


def get_pattern_execution_context_resolver() -> PatternExecutionContextResolver:
    """Return the shared resolver (stateless, safe to share)."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = PatternExecutionContextResolver()
    return _default_resolver
