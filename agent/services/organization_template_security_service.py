"""Template provenance and untrusted-content controls for organization roles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from agent.common.redaction import VisibilityLevel, redact


@dataclass(frozen=True, slots=True)
class RoleInstructionProvenance:
    template_key: str
    template_version: str
    template_hash: str
    appendix_refs: tuple[str, ...]
    governance_stack_version: str


@dataclass(frozen=True, slots=True)
class TemplateSecurityDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    provenance_hash: str
    audit_details: Mapping[str, object]


class OrganizationTemplateSecurityService:
    """Scans imported governance instructions without executing their text."""

    _FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "template_policy_override",
            re.compile(
                r"\b(?:ignore|bypass|override|disable)\b.{0,50}"
                r"\b(?:policy|governance|approval)\b",
                re.I | re.S,
            ),
        ),
        (
            "template_credential_request",
            re.compile(
                r"\b(?:api[_ -]?key|bearer token|password|secret|credential)\b",
                re.I,
            ),
        ),
        (
            "template_direct_worker_address",
            re.compile(
                r"\b(?:worker[_ -]?url|agent[_ -]?url|directly contact|worker-to-worker)\b",
                re.I,
            ),
        ),
        (
            "template_queue_write_directive",
            re.compile(
                r"\b(?:write|insert|enqueue|push)\b.{0,40}"
                r"\b(?:hub )?(?:queue|task db|task database)\b",
                re.I | re.S,
            ),
        ),
        (
            "template_tool_escalation",
            re.compile(
                r"\b(?:grant|enable|add)\b.{0,40}"
                r"\b(?:tool|capability|context scope|permission)\b",
                re.I | re.S,
            ),
        ),
    )
    _APPENDIX_REF = re.compile(r"\{\{\s*appendix:([a-zA-Z0-9_.-]{1,128})\s*\}\}")

    def validate(
        self,
        *,
        provenance: RoleInstructionProvenance,
        instruction_text: str,
        allowed_appendix_refs: Iterable[str],
    ) -> TemplateSecurityDecision:
        reasons: list[str] = []
        text = str(instruction_text or "")
        actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not provenance.template_key or not provenance.template_version or not provenance.governance_stack_version:
            reasons.append("template_provenance_binding_missing")
        if provenance.template_hash != actual_hash:
            reasons.append("template_hash_mismatch")
        allowed_appendices = {str(value or "").strip() for value in allowed_appendix_refs}
        for appendix in provenance.appendix_refs:
            if appendix not in allowed_appendices:
                reasons.append(f"template_appendix_not_allowed:{appendix}")
        for reason_code, pattern in self._FORBIDDEN_PATTERNS:
            if pattern.search(text):
                reasons.append(reason_code)
        provenance_payload = {
            "template_key": provenance.template_key,
            "template_version": provenance.template_version,
            "template_hash": provenance.template_hash,
            "appendix_refs": list(provenance.appendix_refs),
            "governance_stack_version": provenance.governance_stack_version,
        }
        provenance_hash = hashlib.sha256(
            json.dumps(provenance_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        audit = redact(
            {
                **provenance_payload,
                "provenance_hash": provenance_hash,
                "decision": "deny" if reasons else "allow",
                "reason_codes": sorted(set(reasons)),
                "instruction_length": len(text),
                # Deliberately no instruction body.
            },
            VisibilityLevel.USER,
        )
        return TemplateSecurityDecision(
            allowed=not reasons,
            reason_codes=tuple(sorted(set(reasons))),
            provenance_hash=provenance_hash,
            audit_details=audit,
        )

    def validate_role_definition(
        self,
        *,
        template_key: str,
        template_version: int | str,
        definition: Mapping[str, Any],
        allowed_appendix_refs: Iterable[str],
        governance_stack_version: str = "organization_bundle_v2",
    ) -> TemplateSecurityDecision:
        """Validate a portable role definition as inert data before persistence.

        The portable revision's canonical content hash is checked by the bundle
        planner separately.  This method binds and scans the executable prompt
        layer itself and rejects references to appendices that are not already
        installed in the target Hub governance stack.
        """

        instruction_text = str(definition.get("prompt_template") or "")
        appendix_refs = tuple(sorted(set(self._APPENDIX_REF.findall(instruction_text))))
        return self.validate(
            provenance=RoleInstructionProvenance(
                template_key=str(template_key or "").strip(),
                template_version=str(template_version or "").strip(),
                template_hash=hashlib.sha256(instruction_text.encode("utf-8")).hexdigest(),
                appendix_refs=appendix_refs,
                governance_stack_version=str(governance_stack_version or "").strip(),
            ),
            instruction_text=instruction_text,
            allowed_appendix_refs=allowed_appendix_refs,
        )

    @staticmethod
    def wrap_untrusted_content(*, kind: str, content: str, content_hash: str | None = None) -> dict[str, str]:
        """Mark research/PoC/user text as data, never as an instruction layer."""

        text = str(content or "")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if content_hash and content_hash != digest:
            raise ValueError("untrusted_content_hash_mismatch")
        return {
            "kind": str(kind or "untrusted_data"),
            "trust": "untrusted_data",
            "content": text,
            "content_hash": digest,
            "instruction_authority": "none",
        }


def installed_template_appendix_refs(catalog: Any) -> frozenset[str]:
    """Return appendix identifiers already trusted by the target catalog.

    Catalog snapshots intentionally do not expose appendix bodies through the
    portable bundle contract.  Existing validated templates nevertheless form
    a closed allow-list of appendix identifiers available on the target Hub.
    """

    try:
        templates = catalog.snapshot().role_templates.values()
    except (AttributeError, TypeError, ValueError):
        return frozenset()
    return frozenset(
        reference
        for template in templates
        if isinstance(template, Mapping)
        for reference in OrganizationTemplateSecurityService._APPENDIX_REF.findall(
            str(template.get("prompt_template") or "")
        )
    )


__all__ = [
    "OrganizationTemplateSecurityService",
    "RoleInstructionProvenance",
    "TemplateSecurityDecision",
    "installed_template_appendix_refs",
]
