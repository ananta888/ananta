"""Deterministic capability and risk profiling for inventoried scientific skills."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from agent.services.scientific_skill_manifest_service import ScientificSkillManifest

RISK_PROFILE_VERSION = "ananta.scientific-skill-risk.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEPENDENCY_DECLARATION = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@_./:-]{0,127}$")
_CAPABILITIES = frozenset(
    {
        "network",
        "browser",
        "local_process",
        "python_dependency",
        "node_dependency",
        "cli_dependency",
        "file_read",
        "file_write",
        "credential_access",
        "medical_regulatory",
    }
)
_CLASSIFICATION_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_STDLIB = frozenset(
    {
        "argparse",
        "collections",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "hashlib",
        "io",
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "statistics",
        "sys",
        "typing",
        "urllib",
    }
)


class ScientificSkillRiskProfileError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ScientificSkillOperatingMode(str, Enum):
    DOCUMENTATION_ONLY = "documentation-only"
    READ_ONLY_RESEARCH = "read-only-research"
    CONTROLLED_EXECUTION = "controlled-execution"
    BLOCKED = "blocked"


_MODE_ORDER = {
    ScientificSkillOperatingMode.DOCUMENTATION_ONLY: 0,
    ScientificSkillOperatingMode.READ_ONLY_RESEARCH: 1,
    ScientificSkillOperatingMode.CONTROLLED_EXECUTION: 2,
    ScientificSkillOperatingMode.BLOCKED: 3,
}


class ManualAssessmentReason(str, Enum):
    CAPABILITY_REVIEW = "capability_review"
    DEPENDENCY_REVIEW = "dependency_review"
    RISK_RESTRICTION = "risk_restriction"


@dataclass(frozen=True)
class ScientificSkillDependency:
    ecosystem: str
    name: str

    @property
    def declaration(self) -> str:
        return f"{self.ecosystem}:{self.name}"


@dataclass(frozen=True)
class ScientificSkillManualAssessment:
    assessment_id: str
    reviewer_id: str
    skill_sha256: str
    confirmed_capabilities: tuple[str, ...]
    confirmed_dependencies: tuple[str, ...]
    confirmed_data_classification: str | None
    maximum_mode: ScientificSkillOperatingMode
    reason_code: ManualAssessmentReason
    assessment_digest: str

    @classmethod
    def create(
        cls,
        *,
        assessment_id: str,
        reviewer_id: str,
        skill_sha256: str,
        confirmed_capabilities: tuple[str, ...],
        confirmed_dependencies: tuple[str, ...],
        confirmed_data_classification: str | None,
        maximum_mode: ScientificSkillOperatingMode,
        reason_code: ManualAssessmentReason,
    ) -> "ScientificSkillManualAssessment":
        normalized_capabilities = tuple(sorted(confirmed_capabilities))
        normalized_dependencies = tuple(sorted(confirmed_dependencies))
        projection = {
            "assessment_id": assessment_id,
            "reviewer_id": reviewer_id,
            "skill_sha256": skill_sha256,
            "confirmed_capabilities": normalized_capabilities,
            "confirmed_dependencies": normalized_dependencies,
            "confirmed_data_classification": confirmed_data_classification,
            "maximum_mode": maximum_mode.value,
            "reason_code": reason_code.value,
        }
        return cls(
            assessment_id,
            reviewer_id,
            skill_sha256,
            normalized_capabilities,
            normalized_dependencies,
            confirmed_data_classification,
            maximum_mode,
            reason_code,
            _digest(projection),
        )


@dataclass(frozen=True)
class ScientificSkillRiskProfile:
    profile_version: str
    skill_name: str
    skill_sha256: str
    operating_mode: ScientificSkillOperatingMode
    detected_capabilities: tuple[str, ...]
    dependencies: tuple[ScientificSkillDependency, ...]
    network_targets: tuple[str, ...]
    credential_requirements: tuple[str, ...]
    data_classification: str
    context_budget_tokens: int
    reason_codes: tuple[str, ...]
    manual_assessment_id: str | None
    profile_digest: str


class ScientificSkillRiskProfiler:
    """Profiles hash-bound declared text without executing it or consulting an LLM."""

    MAX_CONTEXT_TOKENS = 250_000

    def profile(
        self,
        *,
        manifest: ScientificSkillManifest,
        declared_contents: Mapping[str, bytes],
        manual_assessment: ScientificSkillManualAssessment | None = None,
    ) -> ScientificSkillRiskProfile:
        texts = self._verified_texts(manifest, declared_contents)
        assessment = self._validated_assessment(manifest, manual_assessment)
        combined = "\n".join(texts[path] for path in sorted(texts))
        dependencies = self._dependencies(texts)
        capabilities = self._capabilities(manifest, combined, dependencies)
        network_targets, invalid_network_reference = self._network_targets(manifest, combined)
        credential_requirements = tuple(
            sorted(
                set(
                    re.findall(
                        r"\b[A-Z][A-Z0-9_]{2,63}(?:KEY|TOKEN|SECRET|PASSWORD)\b",
                        combined,
                    )
                )
            )
        )
        context_budget = (sum(len(content) for content in declared_contents.values()) + 3) // 4
        classification = self._classification(capabilities, combined)
        reasons: set[str] = set()
        if invalid_network_reference:
            reasons.add("network_reference_invalid")

        declared_capabilities = set(manifest.declared_capabilities)
        declared_dependencies = set(manifest.declared_dependencies)
        if assessment is not None:
            declared_capabilities.update(assessment.confirmed_capabilities)
            declared_dependencies.update(assessment.confirmed_dependencies)
        unknown_declarations = declared_capabilities - _CAPABILITIES
        reasons.update(f"unknown_declared_capability:{value}" for value in unknown_declarations)
        reasons.update(f"undeclared_capability:{value}" for value in capabilities - declared_capabilities)
        reasons.update(
            f"undeclared_dependency:{dependency.declaration}"
            for dependency in dependencies
            if dependency.declaration not in declared_dependencies
        )
        if any(item.language == "unknown" for item in manifest.declared_files if item.kind == "script"):
            reasons.add("unanalysable_script_language")
        if manifest.parser_warnings:
            reasons.add("manifest_parser_warning")
        if re.search(r"(?i)\b(?:ignore previous|bypass (?:the )?policy|disable security|you are now)\b", combined):
            reasons.add("instruction_override_pattern")
        if re.search(r"(?i)\b(?:eval|exec|compile)\s*\(", combined):
            reasons.add("dynamic_execution_unanalysable")
        if "medical_regulatory" in capabilities:
            reasons.add("medical_regulatory_execution_denied")
        if context_budget > self.MAX_CONTEXT_TOKENS:
            reasons.add("context_budget_exceeded")
        declared_classification = manifest.declared_data_classification
        if assessment is not None and assessment.confirmed_data_classification is not None:
            declared_classification = assessment.confirmed_data_classification
        if _CLASSIFICATION_ORDER[declared_classification] < _CLASSIFICATION_ORDER[classification]:
            reasons.add("data_classification_underdeclared")

        mode = self._mode(capabilities, manifest, combined)
        if assessment is not None and _MODE_ORDER[mode] > _MODE_ORDER[assessment.maximum_mode]:
            reasons.add("manual_assessment_mode_limit")
        if assessment is not None and assessment.maximum_mode is ScientificSkillOperatingMode.BLOCKED:
            reasons.add("manual_assessment_block")
        if reasons:
            mode = ScientificSkillOperatingMode.BLOCKED
        reason_codes = tuple(sorted(reasons))
        projection = {
            "profile_version": RISK_PROFILE_VERSION,
            "skill_name": manifest.name,
            "skill_sha256": manifest.sha256,
            "operating_mode": mode.value,
            "detected_capabilities": sorted(capabilities),
            "dependencies": [dependency.declaration for dependency in dependencies],
            "network_targets": list(network_targets),
            "credential_requirements": list(credential_requirements),
            "data_classification": classification,
            "context_budget_tokens": context_budget,
            "reason_codes": list(reason_codes),
            "manual_assessment_id": None if assessment is None else assessment.assessment_id,
        }
        return ScientificSkillRiskProfile(
            profile_version=RISK_PROFILE_VERSION,
            skill_name=manifest.name,
            skill_sha256=manifest.sha256,
            operating_mode=mode,
            detected_capabilities=tuple(sorted(capabilities)),
            dependencies=dependencies,
            network_targets=network_targets,
            credential_requirements=credential_requirements,
            data_classification=classification,
            context_budget_tokens=context_budget,
            reason_codes=reason_codes,
            manual_assessment_id=None if assessment is None else assessment.assessment_id,
            profile_digest=_digest(projection),
        )

    @staticmethod
    def _verified_texts(
        manifest: ScientificSkillManifest,
        contents: Mapping[str, bytes],
    ) -> dict[str, str]:
        expected = {item.relative_path: item for item in manifest.declared_files}
        if set(contents) != set(expected):
            raise ScientificSkillRiskProfileError("scientific_skill_profile_content_set_mismatch")
        texts: dict[str, str] = {}
        for path, metadata in expected.items():
            content = contents[path]
            if (
                not isinstance(content, bytes)
                or len(content) != metadata.size_bytes
                or hashlib.sha256(content).hexdigest() != metadata.sha256
            ):
                raise ScientificSkillRiskProfileError("scientific_skill_profile_content_digest_mismatch")
            try:
                texts[path] = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ScientificSkillRiskProfileError("scientific_skill_profile_content_invalid") from exc
        return texts

    @staticmethod
    def _validated_assessment(
        manifest: ScientificSkillManifest,
        assessment: ScientificSkillManualAssessment | None,
    ) -> ScientificSkillManualAssessment | None:
        if assessment is None:
            return None
        if (
            assessment.skill_sha256 != manifest.sha256
            or not _IDENTIFIER.fullmatch(assessment.assessment_id)
            or not _IDENTIFIER.fullmatch(assessment.reviewer_id)
            or not set(assessment.confirmed_capabilities).issubset(_CAPABILITIES)
            or any(not _DEPENDENCY_DECLARATION.fullmatch(value) for value in assessment.confirmed_dependencies)
            or (
                assessment.confirmed_data_classification is not None
                and assessment.confirmed_data_classification not in _CLASSIFICATION_ORDER
            )
        ):
            raise ScientificSkillRiskProfileError("scientific_skill_manual_assessment_invalid")
        expected = ScientificSkillManualAssessment.create(
            assessment_id=assessment.assessment_id,
            reviewer_id=assessment.reviewer_id,
            skill_sha256=assessment.skill_sha256,
            confirmed_capabilities=assessment.confirmed_capabilities,
            confirmed_dependencies=assessment.confirmed_dependencies,
            confirmed_data_classification=assessment.confirmed_data_classification,
            maximum_mode=assessment.maximum_mode,
            reason_code=assessment.reason_code,
        )
        if expected.assessment_digest != assessment.assessment_digest:
            raise ScientificSkillRiskProfileError("scientific_skill_manual_assessment_digest_invalid")
        return assessment

    @staticmethod
    def _capabilities(
        manifest: ScientificSkillManifest,
        content: str,
        dependencies: tuple[ScientificSkillDependency, ...],
    ) -> set[str]:
        lowered = content.casefold()
        detected: set[str] = set()
        if any(item.kind == "script" for item in manifest.declared_files):
            detected.add("local_process")
        patterns = {
            "network": r"\b(?:requests\.|httpx\.|urllib\.|socket\.|curl\s|wget\s|fetch\s*\(|api[_ -]?request)\b",
            "browser": r"\b(?:selenium|playwright|browser\.(?:open|navigate)|webbrowser\.)\b",
            "local_process": r"\b(?:subprocess\.|os\.system|popen\s*\(|child_process|spawn\s*\()",
            "file_read": r"\b(?:open\s*\([^\n]*(?:['\"]r|read_text|read_bytes|fs\.read))",
            "file_write": r"\b(?:write_text|write_bytes|fs\.write|open\s*\([^\n]*(?:['\"]w|['\"]a))",
            "credential_access": (
                r"\b(?:api[_-]?key|access[_-]?token|password|credential|secret[_-]?resolver|os\.environ)\b"
            ),
            "medical_regulatory": r"\b(?:diagnos|therap|patient|clinical|hipaa|fda|medical|drug|regulatory)\w*\b",
        }
        for capability, pattern in patterns.items():
            if re.search(pattern, lowered):
                detected.add(capability)
        detected.update(
            f"{item.ecosystem}_dependency"
            for item in dependencies
            if item.ecosystem in {"python", "node", "cli"}
        )
        return detected

    @staticmethod
    def _dependencies(texts: Mapping[str, str]) -> tuple[ScientificSkillDependency, ...]:
        found: set[tuple[str, str]] = set()
        for path, content in texts.items():
            suffix = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
            if suffix == "py":
                for name in re.findall(r"(?m)^\s*(?:from|import)\s+([A-Za-z0-9_.-]+)", content):
                    root = name.split(".", 1)[0]
                    if root not in _STDLIB:
                        found.add(("python", root))
            if suffix in {"js", "mjs", "ts"}:
                for name in re.findall(r"(?:from\s+|require\s*\(\s*)['\"](@?[A-Za-z0-9_.\-/]+)", content):
                    if not name.startswith((".", "/", "node:")):
                        found.add(("node", name.split("/", 1)[0] if not name.startswith("@") else name))
            for command in re.findall(r"(?m)^\s*(curl|wget|git|pip|npm|npx|Rscript)\b", content):
                found.add(("cli", command.casefold()))
        return tuple(ScientificSkillDependency(ecosystem, name) for ecosystem, name in sorted(found))

    @staticmethod
    def _network_targets(
        manifest: ScientificSkillManifest,
        content: str,
    ) -> tuple[tuple[str, ...], bool]:
        urls = set(manifest.source_references)
        urls.update(re.findall(r"https?://[^\s)'\"<>]+", content))
        targets: set[str] = set()
        invalid = False
        for url in urls:
            try:
                target = urlsplit(url).hostname
            except ValueError:
                invalid = True
                continue
            if target:
                targets.add(target)
        return tuple(sorted(targets)), invalid

    @staticmethod
    def _classification(capabilities: set[str], content: str) -> str:
        if "medical_regulatory" in capabilities or "credential_access" in capabilities:
            return "restricted"
        if re.search(r"(?i)\b(?:personal data|customer data|financial data|private source)\b", content):
            return "confidential"
        if capabilities.intersection({"network", "file_read", "file_write"}):
            return "confidential"
        return "internal"

    @staticmethod
    def _mode(
        capabilities: set[str],
        manifest: ScientificSkillManifest,
        content: str,
    ) -> ScientificSkillOperatingMode:
        controlled = {
            "network",
            "browser",
            "local_process",
            "python_dependency",
            "node_dependency",
            "cli_dependency",
            "file_write",
            "credential_access",
        }
        if capabilities.intersection(controlled):
            return ScientificSkillOperatingMode.CONTROLLED_EXECUTION
        if "file_read" in capabilities or manifest.source_references or re.search(
            r"(?i)\b(?:research|literature|evidence|database lookup|bibliograph|citation)\b",
            content,
        ):
            return ScientificSkillOperatingMode.READ_ONLY_RESEARCH
        return ScientificSkillOperatingMode.DOCUMENTATION_ONLY


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


__all__ = [
    "ManualAssessmentReason",
    "RISK_PROFILE_VERSION",
    "ScientificSkillDependency",
    "ScientificSkillManualAssessment",
    "ScientificSkillOperatingMode",
    "ScientificSkillRiskProfile",
    "ScientificSkillRiskProfileError",
    "ScientificSkillRiskProfiler",
]
