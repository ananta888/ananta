"""HAC-004: Evidence-, Tenant-, Revision- und Security-Regeln für hierarchische Architekturkontexte.

Dieses Modul stellt sicher, dass hierarchische Architektur-Nodes nur aus freigegebener
Evidence erstellt werden, denselben Tenant/Workspace/Revision-Scope beibehalten und
Security-Policies (Redaction, Secret-Detection, Injection-Protection) einhalten.

Contract: docs/contracts/codecompass-hierarchical-architecture-context.md
Schema: codecompass.hierarchical-architecture-context.v1
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# Schema-Konstanten
SCHEMA_HIERARCHICAL_ARCH_CONTEXT = "codecompass_hierarchical_arch_context.v1"

# Evidence-Kinds für Architektur-Nodes
EVIDENCE_KIND_FILE_RANGE = "file_range"
EVIDENCE_KIND_SYMBOL_REF = "symbol_ref"
EVIDENCE_KIND_GRAPH_EDGE = "graph_edge"
EVIDENCE_KIND_DOMAIN_MAP = "domain_map"
EVIDENCE_KIND_SUMMARY_DERIVED = "summary_derived"

# Security-Konstanten
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)\s*[:=]\s*['\"]?([^\s'\";]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)(aws[_-]?access[_-]?key[_-]?id)\s*[:=]\s*['\"]?([A-Z0-9]{16,})"),
    re.compile(r"(?i)(private[_-]?key|-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----)"),
]

INJECTION_PATTERNS = [
    re.compile(r"(?i)(ignore\s+(previous|all)\s+instructions)"),
    re.compile(r"(?i)(you\s+are\s+now\s+(free|unrestricted))"),
    re.compile(r"(?i)(system\s*:\s*(override|bypass))"),
]


@dataclass(frozen=True)
class ArchitectureSecurityPolicy:
    """Security-Policy für hierarchische Architekturkontexte."""
    
    require_tenant_consistency: bool = True
    require_revision_consistency: bool = True
    require_workspace_scope: bool = True
    redact_secrets: bool = True
    detect_injection: bool = True
    fail_closed_on_violation: bool = True
    allowed_source_authorities: list[str] = field(default_factory=lambda: ["workspace", "git", "indexed"])
    max_summary_length: int = 500
    require_evidence_for_summary: bool = True
    
    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None = None) -> "ArchitectureSecurityPolicy":
        data = dict(raw or {})
        
        def _bool(name: str, default: bool) -> bool:
            value = data.get(name, default)
            if isinstance(value, bool):
                return value
            token = str(value).strip().lower()
            return token in {"1", "true", "yes", "on", "an", "ja"}
        
        def _int(name: str, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(int(data.get(name, default)), hi))
            except (TypeError, ValueError):
                return default
        
        # Default-Wert holen, nicht Klassenattribut
        default_allowed = ["workspace", "git", "indexed"]
        allowed = data.get("allowed_source_authorities", default_allowed)
        if not isinstance(allowed, list):
            allowed = default_allowed
        
        return cls(
            require_tenant_consistency=_bool("require_tenant_consistency", cls.require_tenant_consistency),
            require_revision_consistency=_bool("require_revision_consistency", cls.require_revision_consistency),
            require_workspace_scope=_bool("require_workspace_scope", cls.require_workspace_scope),
            redact_secrets=_bool("redact_secrets", cls.redact_secrets),
            detect_injection=_bool("detect_injection", cls.detect_injection),
            fail_closed_on_violation=_bool("fail_closed_on_violation", cls.fail_closed_on_violation),
            allowed_source_authorities=[str(a) for a in allowed],
            max_summary_length=_int("max_summary_length", cls.max_summary_length, 50, 5000),
            require_evidence_for_summary=_bool("require_evidence_for_summary", cls.require_evidence_for_summary),
        )


@dataclass(frozen=True)
class EvidenceChain:
    """Evidence-Kette für einen Architektur-Node."""
    
    kind: Literal["file_range", "symbol_ref", "graph_edge", "domain_map", "summary_derived"]
    source_authority: str
    workspace: str
    revision: str
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    symbol_id: str | None = None
    graph_path: list[str] | None = None
    confidence: float = 1.0
    redacted: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "kind": self.kind,
            "source_authority": self.source_authority,
            "workspace": self.workspace,
            "revision": self.revision,
            "confidence": self.confidence,
        }
        if self.path is not None:
            entry["path"] = self.path
        if self.line_start is not None:
            entry["line_start"] = self.line_start
        if self.line_end is not None:
            entry["line_end"] = self.line_end
        if self.symbol_id is not None:
            entry["symbol_id"] = self.symbol_id
        if self.graph_path is not None:
            entry["graph_path"] = self.graph_path
        if self.redacted:
            entry["redacted"] = True
        return entry


@dataclass
class SecurityCheckResult:
    """Ergebnis einer Security-Prüfung."""
    
    passed: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    redacted_content: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "warnings": self.warnings,
            "redacted_applied": self.redacted_content is not None,
        }


class ArchitectureSecurityGate:
    """Security-Gate für hierarchische Architekturkontexte.
    
    Stellt sicher, dass:
    - Tenant/Workspace/Revision konsistent bleiben
    - Secrets automatisch erkannt und redacted werden
    - Prompt-Injection-Versuche blockiert werden
    - Evidence-Anforderungen erfüllt sind
    """
    
    def __init__(self, policy: ArchitectureSecurityPolicy | None = None):
        self._policy = policy or ArchitectureSecurityPolicy()
    
    @property
    def policy(self) -> ArchitectureSecurityPolicy:
        return self._policy
    
    def check_tenant_consistency(
        self,
        *,
        expected_tenant: str,
        actual_tenant: str,
        node_id: str,
    ) -> SecurityCheckResult:
        """Prüft Tenant-Konsistenz."""
        violations = []
        warnings = []
        
        if self._policy.require_tenant_consistency and expected_tenant != actual_tenant:
            violations.append({
                "type": "tenant_mismatch",
                "node_id": node_id,
                "expected": expected_tenant,
                "actual": actual_tenant,
                "severity": "critical",
            })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def check_revision_consistency(
        self,
        *,
        expected_revision: str,
        actual_revision: str,
        node_id: str,
    ) -> SecurityCheckResult:
        """Prüft Revision-Konsistenz."""
        violations = []
        warnings = []
        
        if self._policy.require_revision_consistency and expected_revision != actual_revision:
            violations.append({
                "type": "revision_mismatch",
                "node_id": node_id,
                "expected": expected_revision,
                "actual": actual_revision,
                "severity": "high",
            })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def check_workspace_scope(
        self,
        *,
        allowed_workspaces: list[str],
        actual_workspace: str,
        node_id: str,
    ) -> SecurityCheckResult:
        """Prüft Workspace-Scope."""
        violations = []
        warnings = []
        
        if self._policy.require_workspace_scope and actual_workspace not in allowed_workspaces:
            violations.append({
                "type": "workspace_out_of_scope",
                "node_id": node_id,
                "allowed": allowed_workspaces,
                "actual": actual_workspace,
                "severity": "high",
            })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def check_source_authority(
        self,
        *,
        source_authority: str,
        node_id: str,
    ) -> SecurityCheckResult:
        """Prüft Source Authority."""
        violations = []
        warnings = []
        
        if source_authority not in self._policy.allowed_source_authorities:
            violations.append({
                "type": "unauthorized_source",
                "node_id": node_id,
                "source": source_authority,
                "allowed": self._policy.allowed_source_authorities,
                "severity": "high",
            })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def detect_and_redact_secrets(self, content: str) -> tuple[str, list[dict[str, Any]]]:
        """Erkennt und redacted Secrets im Content."""
        if not self._policy.redact_secrets:
            return content, []
        
        found_secrets = []
        redacted = content
        
        for pattern in SECRET_PATTERNS:
            matches = list(pattern.finditer(redacted))
            for match in matches:
                secret_type = match.group(1) if match.lastindex >= 1 else "secret"
                found_secrets.append({
                    "type": "detected_secret",
                    "pattern": str(pattern.pattern[:50]),
                    "position": match.start(),
                    "secret_type": secret_type,
                })
                # Redact: Ersetze durch Platzhalter
                if match.lastindex >= 2:
                    # Pattern hat Capture-Groups
                    start, end = match.span()
                    redacted = redacted[:start] + f"[REDACTED_{secret_type.upper()}]" + redacted[end:]
                else:
                    redacted = redacted[:match.start()] + "[REDACTED_SECRET]" + redacted[match.end():]
        
        return redacted, found_secrets
    
    def detect_injection_attempts(self, content: str) -> SecurityCheckResult:
        """Erkennt Prompt-Injection-Versuche."""
        violations = []
        warnings = []
        
        if not self._policy.detect_injection:
            return SecurityCheckResult(passed=True, violations=[], warnings=[])
        
        for pattern in INJECTION_PATTERNS:
            matches = list(pattern.finditer(content))
            for match in matches:
                violations.append({
                    "type": "injection_attempt",
                    "pattern": str(pattern.pattern[:50]),
                    "position": match.start(),
                    "matched_text": match.group(0)[:100],
                    "severity": "critical",
                })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def validate_evidence_for_summary(
        self,
        *,
        summary: str,
        evidence_chain: list[EvidenceChain],
        node_id: str,
    ) -> SecurityCheckResult:
        """Validiert, dass Summary auf Evidence basiert."""
        violations = []
        warnings = []
        
        if not self._policy.require_evidence_for_summary and len(evidence_chain) == 0:
            warnings.append("no_evidence_but_allowed_by_policy")
            return SecurityCheckResult(passed=True, violations=violations, warnings=warnings)
        
        if len(summary) > 0 and len(evidence_chain) == 0:
            violations.append({
                "type": "missing_evidence",
                "node_id": node_id,
                "summary_length": len(summary),
                "message": "Summary requires at least one evidence entry",
                "severity": "high",
            })
        
        # Prüfe Evidence-Integrität
        for idx, evidence in enumerate(evidence_chain):
            if evidence.source_authority not in self._policy.allowed_source_authorities:
                violations.append({
                    "type": "invalid_evidence_source",
                    "node_id": node_id,
                    "evidence_index": idx,
                    "source": evidence.source_authority,
                    "severity": "high",
                })
        
        return SecurityCheckResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
    
    def check_node_security(
        self,
        *,
        node_id: str,
        node_level: str,
        node_title: str,
        node_summary: str,
        tenant: str,
        workspace: str,
        revision: str,
        source_authority: str,
        evidence_chain: list[EvidenceChain],
        expected_tenant: str,
        expected_revision: str,
        allowed_workspaces: list[str],
    ) -> SecurityCheckResult:
        """Umfassende Security-Prüfung für einen Node."""
        all_violations = []
        all_warnings = []
        
        # Tenant-Check
        tenant_result = self.check_tenant_consistency(
            expected_tenant=expected_tenant,
            actual_tenant=tenant,
            node_id=node_id,
        )
        all_violations.extend(tenant_result.violations)
        all_warnings.extend(tenant_result.warnings)
        
        # Revision-Check
        revision_result = self.check_revision_consistency(
            expected_revision=expected_revision,
            actual_revision=revision,
            node_id=node_id,
        )
        all_violations.extend(revision_result.violations)
        all_warnings.extend(revision_result.warnings)
        
        # Workspace-Check
        workspace_result = self.check_workspace_scope(
            allowed_workspaces=allowed_workspaces,
            actual_workspace=workspace,
            node_id=node_id,
        )
        all_violations.extend(workspace_result.violations)
        all_warnings.extend(workspace_result.warnings)
        
        # Source Authority Check
        source_result = self.check_source_authority(
            source_authority=source_authority,
            node_id=node_id,
        )
        all_violations.extend(source_result.violations)
        all_warnings.extend(source_result.warnings)
        
        # Evidence Validation
        evidence_result = self.validate_evidence_for_summary(
            summary=node_summary,
            evidence_chain=evidence_chain,
            node_id=node_id,
        )
        all_violations.extend(evidence_result.violations)
        all_warnings.extend(evidence_result.warnings)
        
        # Injection Detection in Title und Summary
        for content_field, field_name in [(node_title, "title"), (node_summary, "summary")]:
            injection_result = self.detect_injection_attempts(content_field)
            all_violations.extend(injection_result.violations)
            all_warnings.extend(injection_result.warnings)
        
        # Secret Detection in Summary
        redacted_summary, found_secrets = self.detect_and_redact_secrets(node_summary)
        if found_secrets:
            all_warnings.append(f"secrets_detected_and_redacted_in_{field_name}")
            for secret in found_secrets:
                all_violations.append({
                    "type": "secret_detected",
                    "node_id": node_id,
                    "field": field_name,
                    "details": secret,
                    "severity": "medium",
                })
        
        # Entscheidung: Pass/Fail
        critical_violations = [v for v in all_violations if v.get("severity") == "critical"]
        high_violations = [v for v in all_violations if v.get("severity") == "high"]
        
        if self._policy.fail_closed_on_violation and (critical_violations or high_violations):
            passed = False
        else:
            passed = len(critical_violations) == 0
        
        return SecurityCheckResult(
            passed=passed,
            violations=all_violations,
            warnings=all_warnings,
            redacted_content=redacted_summary if redacted_summary != node_summary else None,
        )


def build_secure_architecture_node(
    *,
    node_id: str,
    level: str,
    title: str,
    summary: str,
    responsibilities: list[str],
    evidence_chain: list[dict[str, Any]],
    tenant: str,
    workspace: str,
    revision: str,
    source_authority: str,
    security_gate: ArchitectureSecurityGate,
    expected_tenant: str,
    expected_revision: str,
    allowed_workspaces: list[str],
) -> tuple[dict[str, Any] | None, SecurityCheckResult]:
    """Erstellt einen sicheren Architektur-Node mit Security-Checks.
    
    Returns:
        Tuple aus (Node-Dict oder None bei Security-Failure, SecurityCheckResult)
    """
    # Evidence-Chains konvertieren
    chains = []
    for ev in evidence_chain:
        chain = EvidenceChain(
            kind=ev.get("kind", "summary_derived"),
            source_authority=ev.get("source_authority", source_authority),
            workspace=ev.get("workspace", workspace),
            revision=ev.get("revision", revision),
            path=ev.get("path"),
            line_start=ev.get("line_start"),
            line_end=ev.get("line_end"),
            symbol_id=ev.get("symbol_id"),
            graph_path=ev.get("graph_path"),
            confidence=ev.get("confidence", 1.0),
            redacted=ev.get("redacted", False),
        )
        chains.append(chain)
    
    # Security-Check durchführen
    security_result = security_gate.check_node_security(
        node_id=node_id,
        node_level=level,
        node_title=title,
        node_summary=summary,
        tenant=tenant,
        workspace=workspace,
        revision=revision,
        source_authority=source_authority,
        evidence_chain=chains,
        expected_tenant=expected_tenant,
        expected_revision=expected_revision,
        allowed_workspaces=allowed_workspaces,
    )
    
    # Bei Failure und fail_closed: None zurückgeben
    if not security_result.passed and security_gate.policy.fail_closed_on_violation:
        return None, security_result
    
    # Summary ggf. redacten
    final_summary = security_result.redacted_content if security_result.redacted_content else summary
    
    # Node bauen
    node = {
        "id": node_id,
        "level": level,
        "title": title,
        "short_summary": final_summary[:security_gate.policy.max_summary_length],
        "responsibilities": responsibilities,
        "evidence": [chain.to_dict() for chain in chains],
        "source_authority": source_authority,
        "tenant": tenant,
        "workspace": workspace,
        "revision": revision,
        "expandable": True,
        "security_warnings": security_result.warnings,
    }
    
    return node, security_result


# Convenience-Function für Integration
def get_architecture_security_gate(
    policy_config: dict[str, Any] | None = None,
) -> ArchitectureSecurityGate:
    """Factory für ArchitectureSecurityGate mit optionaler Policy-Konfiguration."""
    policy = ArchitectureSecurityPolicy.from_raw(policy_config)
    return ArchitectureSecurityGate(policy=policy)
