from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Literal

from agent.codecompass.semantic_translation.python_adapter import PythonSemanticAdapter
from agent.codecompass.semantic_translation.python_dynamic_detector import detect_dynamic_features
from agent.codecompass.semantic_translation.java_type_registry_python import PythonToJavaTypeRegistry
from agent.codecompass.semantic_translation.rust_type_registry import PythonToRustTypeRegistry
from agent.codecompass.semantic_translation.java_emitter import JavaEmitter
from agent.codecompass.semantic_translation.rust_emitter import RustEmitter

_java_registry = PythonToJavaTypeRegistry()
_rust_registry = PythonToRustTypeRegistry()
_java_emitter = JavaEmitter()
_rust_emitter = RustEmitter()
_adapter = PythonSemanticAdapter()

TransformStatus = Literal["safe_auto_transform", "needs_review", "blocked_dynamic_runtime", "unsupported"]
VerifierStatus = Literal["verified", "verified_with_warnings", "needs_review", "failed"]


@dataclass
class TranslationPlanEntry:
    symbol: str
    kind: str
    status: TransformStatus
    target_language: str
    rules: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    type_confidence: str = "unknown"
    java_artifact: dict | None = None
    rust_artifact: dict | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "status": self.status,
            "target_language": self.target_language,
            "rules": self.rules,
            "warnings": self.warnings,
            "blockers": self.blockers,
            "type_confidence": self.type_confidence,
            "java_artifact": self.java_artifact,
            "rust_artifact": self.rust_artifact,
        }


@dataclass
class TranslationPlan:
    source_path: str
    source_hash: str
    target: str
    entries: list[TranslationPlanEntry] = field(default_factory=list)
    dynamic_blockers: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: list[str] = field(default_factory=list)

    @property
    def is_fully_safe(self) -> bool:
        return not self.dynamic_blockers and all(e.status == "safe_auto_transform" for e in self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "target": self.target,
            "entries": [e.as_dict() for e in self.entries],
            "dynamic_blockers": self.dynamic_blockers,
            "created_at": self.created_at,
            "warnings": self.warnings,
            "is_fully_safe": self.is_fully_safe,
        }


@dataclass
class TransformArtifact:
    source_path: str
    source_hash: str
    target_language: str
    symbol: str
    kind: str
    target_source: str
    rule_ids: list[str]
    warnings: list[str]
    verifier_status: VerifierStatus
    needs_review: bool
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ownership_decisions: list[dict] = field(default_factory=list)

    @property
    def target_hash(self) -> str:
        return hashlib.sha256(self.target_source.encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_hash": self.source_hash,
            "target_hash": self.target_hash,
            "target_language": self.target_language,
            "symbol": self.symbol,
            "kind": self.kind,
            "target_source": self.target_source,
            "rule_ids": self.rule_ids,
            "warnings": self.warnings,
            "verifier_status": self.verifier_status,
            "needs_review": self.needs_review,
            "created_at": self.created_at,
            "ownership_decisions": self.ownership_decisions,
        }


class PythonTranslationPlanService:
    def create_plan(self, source: str, path: str = "<stdin>", target: str = "both") -> TranslationPlan:
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        plan = TranslationPlan(source_path=path, source_hash=source_hash, target=target)

        # Dynamic feature detection first
        dynamic = detect_dynamic_features(source, path)
        if dynamic.has_blockers:
            plan.dynamic_blockers = [f.as_dict() for f in dynamic.features if f.severity == "blocker"]
            plan.warnings.append(f"blocked_by_dynamic_features: {', '.join(dynamic.blocker_codes)}")

        parsed = _adapter.parse(path, source)
        plan.warnings.extend(str(d.get("message") or d) for d in (parsed.get("diagnostics") or []))

        targets = ["java", "rust"] if target == "both" else [target]

        for item in parsed.get("types") or []:
            for lang in targets:
                entry = self._plan_type(item, lang, path, source_hash, dynamic)
                plan.entries.append(entry)

        for fn in parsed.get("functions") or []:
            for lang in targets:
                entry = self._plan_function(fn, lang, path, source_hash, dynamic)
                plan.entries.append(entry)

        return plan

    def _plan_type(self, item: dict, lang: str, path: str, source_hash: str, dynamic: Any) -> TranslationPlanEntry:
        kind = item.get("kind", "class")
        name = item["name"]
        warnings = list(item.get("warnings") or [])
        blockers = list(item.get("unsupported") or [])
        status: TransformStatus = "safe_auto_transform"
        rules: list[str] = []
        type_confidence = _aggregate_confidence(item.get("fields") or [])

        if dynamic.has_blockers:
            status = "blocked_dynamic_runtime"
            blockers = [f.as_dict() for f in dynamic.features if f.severity == "blocker"]
        elif blockers:
            status = "blocked_dynamic_runtime"
        elif kind in ("dataclass", "frozen_dataclass") or (kind == "class" and not item.get("unsupported")):
            if lang == "java":
                rules.append("pyjr.dataclass_to_java_record.v1" if kind in ("dataclass", "frozen_dataclass") else "pyjr.class_to_java_class.v1")
            else:
                rules.append("pyjr.dataclass_to_rust_struct.v1" if kind in ("dataclass", "frozen_dataclass") else "pyjr.class_to_rust_struct.v1")
            if type_confidence in ("unknown", "dynamic"):
                status = "needs_review"
                warnings.append(f"type_confidence_{type_confidence}: annotate fields for safe auto-transform")
        elif kind == "typed_dict":
            rules.append(f"pyjr.typed_dict_to_{'java_record' if lang == 'java' else 'rust_struct'}.v1")
        elif kind == "enum":
            rules.append(f"pyjr.enum_to_{'java_enum' if lang == 'java' else 'rust_enum'}.v1")
        else:
            status = "unsupported"
            warnings.append(f"unsupported_class_kind:{kind}")

        # Produce artifact
        java_artifact = None
        rust_artifact = None
        if status in ("safe_auto_transform", "needs_review"):
            if lang == "java":
                java_artifact = self._emit_java_type(item)
            else:
                rust_artifact = self._emit_rust_type(item)

        return TranslationPlanEntry(
            symbol=name, kind=kind, status=status, target_language=lang,
            rules=rules, warnings=warnings, blockers=[str(b) for b in blockers],
            type_confidence=type_confidence, java_artifact=java_artifact, rust_artifact=rust_artifact,
        )

    def _plan_function(self, fn: dict, lang: str, path: str, source_hash: str, dynamic: Any) -> TranslationPlanEntry:
        name = fn["name"]
        warnings = list(fn.get("warnings") or [])
        status: TransformStatus = "safe_auto_transform"
        rules: list[str] = []
        type_confidence = _param_confidence(fn.get("parameters") or [])

        if dynamic.has_blockers:
            status = "blocked_dynamic_runtime"
        elif fn.get("has_varargs") or fn.get("has_kwargs"):
            status = "needs_review"
            warnings.append("varargs_kwargs_block_auto_transform")
        elif "nested_function_or_lambda_needs_review" in warnings:
            status = "needs_review"
        elif type_confidence in ("unknown", "dynamic"):
            status = "needs_review"
            warnings.append(f"type_confidence_{type_confidence}")
        else:
            rules.append(f"pyjr.function_to_{'java_method' if lang == 'java' else 'rust_fn'}.v1")

        java_artifact = None
        rust_artifact = None
        if status in ("safe_auto_transform", "needs_review"):
            if lang == "java":
                result = _java_emitter.emit_method_signature("Module", fn)
                java_artifact = result.as_dict()
            else:
                result = _rust_emitter.emit_function_signature(fn)
                rust_artifact = result.as_dict()

        return TranslationPlanEntry(
            symbol=name, kind="function", status=status, target_language=lang,
            rules=rules, warnings=warnings, blockers=[],
            type_confidence=type_confidence, java_artifact=java_artifact, rust_artifact=rust_artifact,
        )

    def _emit_java_type(self, item: dict) -> dict:
        kind = item.get("kind", "class")
        if kind == "enum":
            result = _java_emitter.emit_enum(item["name"], item.get("enum_values") or [])
        elif kind in ("dataclass", "frozen_dataclass", "typed_dict"):
            result = _java_emitter.emit_record(item["name"], item.get("fields") or [])
        else:
            result = _java_emitter.emit_class(item["name"], item.get("fields") or [], mutable=kind != "frozen_dataclass")
        return result.as_dict()

    def _emit_rust_type(self, item: dict) -> dict:
        kind = item.get("kind", "class")
        if kind == "enum":
            result = _rust_emitter.emit_enum(item["name"], item.get("enum_values") or [])
        elif kind in ("dataclass", "frozen_dataclass", "typed_dict", "class"):
            result = _rust_emitter.emit_struct(item["name"], item.get("fields") or [], frozen=kind == "frozen_dataclass")
        else:
            result = _rust_emitter.emit_struct(item["name"], item.get("fields") or [])
        return result.as_dict()


class PythonTransformEngine:
    """Deterministic transform engine — applies plan entries and produces trace artifacts."""

    def __init__(self) -> None:
        self._plan_service = PythonTranslationPlanService()

    def transform(self, source: str, path: str = "<stdin>", target: str = "both") -> list[TransformArtifact]:
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        plan = self._plan_service.create_plan(source, path, target)
        artifacts: list[TransformArtifact] = []

        for entry in plan.entries:
            if entry.status == "blocked_dynamic_runtime":
                artifacts.append(TransformArtifact(
                    source_path=path, source_hash=source_hash,
                    target_language=entry.target_language, symbol=entry.symbol, kind=entry.kind,
                    target_source="", rule_ids=[], warnings=entry.warnings + entry.blockers,
                    verifier_status="failed", needs_review=True,
                ))
                continue

            if entry.target_language == "java" and entry.java_artifact:
                art = entry.java_artifact
                verifier_status: VerifierStatus = "verified_with_warnings" if art.get("warnings") else "verified"
                if art.get("needs_review"):
                    verifier_status = "needs_review"
                artifacts.append(TransformArtifact(
                    source_path=path, source_hash=source_hash,
                    target_language="java", symbol=entry.symbol, kind=entry.kind,
                    target_source=art.get("source", ""),
                    rule_ids=entry.rules, warnings=art.get("warnings") or [],
                    verifier_status=verifier_status, needs_review=art.get("needs_review", False),
                ))

            if entry.target_language == "rust" and entry.rust_artifact:
                art = entry.rust_artifact
                verifier_status = "verified_with_warnings" if art.get("warnings") else "verified"
                if art.get("needs_review"):
                    verifier_status = "needs_review"
                artifacts.append(TransformArtifact(
                    source_path=path, source_hash=source_hash,
                    target_language="rust", symbol=entry.symbol, kind=entry.kind,
                    target_source=art.get("source", ""),
                    rule_ids=entry.rules, warnings=art.get("warnings") or [],
                    verifier_status=verifier_status, needs_review=art.get("needs_review", False),
                ))

        return artifacts


def _aggregate_confidence(fields: list[dict]) -> str:
    confidences = [f.get("type_annotation", {}).get("confidence", "unknown") for f in fields]
    if not confidences:
        return "unknown"
    if any(c == "dynamic" for c in confidences):
        return "dynamic"
    if all(c == "annotated" for c in confidences):
        return "annotated"
    if any(c == "unknown" for c in confidences):
        return "unknown"
    return "inferred_from_default"


def _param_confidence(params: list[dict]) -> str:
    non_self = [p for p in params if p.get("kind") != "self"]
    if not non_self:
        return "annotated"
    confidences = [p.get("type_annotation", {}).get("confidence", "unknown") for p in non_self]
    if any(c == "dynamic" for c in confidences):
        return "dynamic"
    if all(c == "annotated" for c in confidences):
        return "annotated"
    if any(c == "unknown" for c in confidences):
        return "unknown"
    return "inferred_from_default"
