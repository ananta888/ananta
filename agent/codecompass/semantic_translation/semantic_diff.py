from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DiffKind = Literal["missing_field", "extra_field", "changed_optionality", "lost_enum_value", "changed_mutability", "changed_type", "lost_default", "ok"]


@dataclass
class SemanticDiffEntry:
    kind: DiffKind
    symbol: str
    source_value: Any = None
    target_value: Any = None
    severity: str = "warning"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "symbol": self.symbol, "source_value": self.source_value, "target_value": self.target_value, "severity": self.severity}


@dataclass
class SemanticDiffResult:
    source_symbol: str
    target_language: str
    entries: list[SemanticDiffEntry] = field(default_factory=list)

    @property
    def has_divergence(self) -> bool:
        return any(e.kind != "ok" for e in self.entries)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == "warning")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_symbol": self.source_symbol,
            "target_language": self.target_language,
            "has_divergence": self.has_divergence,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "entries": [e.as_dict() for e in self.entries],
        }


class SemanticDiffEngine:
    """
    Compares source Python semantic model against a target artifact.
    Detects missing fields, changed optionality, lost enum values, changed mutability.
    """

    def diff(self, source_item: dict, target_artifact: dict, target_language: str) -> SemanticDiffResult:
        name = source_item.get("name", "?")
        result = SemanticDiffResult(source_symbol=name, target_language=target_language)
        target_source = target_artifact.get("source", "")

        self._check_fields(source_item, target_source, result)
        self._check_enum_values(source_item, target_source, result)
        self._check_mutability(source_item, target_artifact, result)
        self._check_defaults(source_item, result)
        self._check_optionality(source_item, target_source, target_language, result)

        return result

    def _check_fields(self, item: dict, target_source: str, result: SemanticDiffResult) -> None:
        for f in item.get("fields") or []:
            fname = f["name"]
            if fname not in target_source:
                result.entries.append(SemanticDiffEntry("missing_field", fname, fname, None, "error"))
            else:
                result.entries.append(SemanticDiffEntry("ok", fname))

    def _check_enum_values(self, item: dict, target_source: str, result: SemanticDiffResult) -> None:
        for v in item.get("enum_values") or []:
            if v not in target_source:
                result.entries.append(SemanticDiffEntry("lost_enum_value", v, v, None, "error"))
            else:
                result.entries.append(SemanticDiffEntry("ok", v))

    def _check_mutability(self, item: dict, artifact: dict, result: SemanticDiffResult) -> None:
        kind = item.get("kind", "class")
        if kind == "frozen_dataclass":
            source = artifact.get("source", "")
            if "mut " in source:
                result.entries.append(SemanticDiffEntry("changed_mutability", item["name"], "frozen", "mutable", "warning"))

    def _check_defaults(self, item: dict, result: SemanticDiffResult) -> None:
        for f in item.get("fields") or []:
            if f.get("has_default") and f.get("default") is not None:
                if "factory" in str(f.get("default", "")):
                    result.entries.append(SemanticDiffEntry("lost_default", f["name"], f["default"], None, "warning"))

    def _check_optionality(self, item: dict, target_source: str, lang: str, result: SemanticDiffResult) -> None:
        for f in item.get("fields") or []:
            type_ann = f.get("type_annotation") or {}
            if not type_ann.get("is_optional"):
                continue
            fname = f["name"]
            if lang == "java" and "Optional<" not in target_source and f"@Nullable" not in target_source:
                result.entries.append(SemanticDiffEntry("changed_optionality", fname, "optional", "non_optional", "warning"))
            elif lang == "rust" and "Option<" not in target_source:
                result.entries.append(SemanticDiffEntry("changed_optionality", fname, "optional", "non_optional", "error"))
