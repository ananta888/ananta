"""RCHCS-003 + RCHCS-007: Generator pipeline for referenced context hints.

Generators
----------
DeterministicHintGenerator
    Produces file_summary, symbol_hint and test_hint from static file
    analysis (imports, class/function names, known test patterns).
    No LLM, no external service required.

LlmHintGenerator
    Optionally calls the configured LLM backend to generate a richer
    summary — only if the path policy permits.  Result is flagged as
    ``generator.kind = "llm"`` and marked as requiring human review
    when confidence is low.

RestrictedInferenceHintGenerator
    Delegates to RestrictedModelInferenceService for classification /
    scoring tasks that do NOT produce free text (e.g., domain label
    classification, sensitivity scoring, hint validation scoring).

ManualHintImporter
    Accepts pre-authored hint dicts, validates them, and returns typed
    ReferencedContextHint records.  All manual hints get
    ``generator.kind = "manual"``.

HybridHintGenerator
    Runs DeterministicHintGenerator first, then optionally enriches with
    LLM or RTI, and marks the result as ``kind = "hybrid"``.
"""
from __future__ import annotations

import ast
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from agent.services.referenced_context_hint_schema import (
    GEN_DETERMINISTIC,
    GEN_HYBRID,
    GEN_LLM,
    GEN_MANUAL,
    GEN_RESTRICTED_TRANSFORMER_INFERENCE,
    KIND_ARCHITECTURE_HINT,
    KIND_DOMAIN_SUMMARY,
    KIND_FILE_SUMMARY,
    KIND_MANUAL_NOTE,
    KIND_RISK_HINT,
    KIND_SYMBOL_HINT,
    KIND_TEST_HINT,
    STALENESS_FRESH,
    CodeCompassRef,
    ConfidenceMetadata,
    GeneratorMetadata,
    HashMetadata,
    HintPolicyMetadata,
    HintValidationError,
    ReferencedContextHint,
    SourceRef,
    ValidityMetadata,
    compute_confidence,
    hash_file,
    hash_text,
    make_hint_id,
    validate_hint,
)

_TOOL_VERSION = "rchcs.generators.v1"

# ── Deterministic generator ───────────────────────────────────────────────────

class DeterministicHintGenerator:
    """Analyse a Python/TypeScript/Markdown file statically and produce hints.

    No LLM key required; no network access.
    """

    def generate_file_summary(
        self,
        file_path: str | Path,
        *,
        workspace_root: str | Path | None = None,
    ) -> ReferencedContextHint | None:
        fp = Path(file_path)
        if not fp.exists():
            return None

        content = self._safe_read(fp)
        if not content:
            return None

        src_hash = hash_file(str(fp))
        rel_path = str(fp.relative_to(workspace_root)) if workspace_root else str(fp)
        hint_id = make_hint_id(rel_path, KIND_FILE_SUMMARY)
        summary = self._summarize_file(fp, content)
        if not summary.strip():
            return None

        source_ref = SourceRef(
            path=rel_path,
            sha256=src_hash,
            role="primary_source",
        )
        confidence_score = compute_confidence(
            source_ref_count=1,
            generator_kind=GEN_DETERMINISTIC,
            staleness_status=STALENESS_FRESH,
        )

        hint = ReferencedContextHint(
            id=hint_id,
            kind=KIND_FILE_SUMMARY,
            title=f"File summary: {fp.name}",
            summary=summary,
            source_refs=[source_ref],
            generator=GeneratorMetadata(
                kind=GEN_DETERMINISTIC,
                tool_version=_TOOL_VERSION,
            ),
            confidence=ConfidenceMetadata(
                score=confidence_score,
                method="static_analysis+source_ref_coverage",
                requires_human_review=False,
            ),
            validity=ValidityMetadata(
                scope="file",
                valid_for_tasks=["navigation", "explanation", "context_selection"],
                not_valid_for=[
                    "authoritative_code_review",
                    "security_claim_without_original_source",
                ],
            ),
            hashes=HashMetadata(
                source_hash=src_hash,
                summary_hash=hash_text(summary),
                context_hash=hash_text(rel_path + summary),
            ),
            policy=HintPolicyMetadata(
                external_cloud_safe=True,
                contains_original_code_excerpt=False,
                sensitivity=self._estimate_sensitivity(rel_path, content),
            ),
        )
        validate_hint(hint)
        return hint

    def generate_symbol_hints(
        self,
        file_path: str | Path,
        *,
        workspace_root: str | Path | None = None,
        max_symbols: int = 20,
    ) -> list[ReferencedContextHint]:
        fp = Path(file_path)
        if not fp.exists() or fp.suffix not in (".py",):
            return []
        content = self._safe_read(fp)
        if not content:
            return []

        symbols = self._extract_python_symbols(content, max_symbols)
        if not symbols:
            return []

        src_hash = hash_file(str(fp))
        rel_path = str(fp.relative_to(workspace_root)) if workspace_root else str(fp)
        hints: list[ReferencedContextHint] = []

        for sym in symbols:
            name = sym["name"]
            sym_type = sym["type"]  # class | function | method
            line_start = sym.get("line_start")
            line_end = sym.get("line_end")
            summary = sym.get("docstring") or f"{sym_type} `{name}` in {fp.name}"

            hint_id = make_hint_id(rel_path, KIND_SYMBOL_HINT, extra=name)
            source_ref = SourceRef(
                path=rel_path,
                sha256=src_hash,
                line_start=line_start,
                line_end=line_end,
                role="primary_source",
            )
            confidence_score = compute_confidence(
                source_ref_count=1,
                generator_kind=GEN_DETERMINISTIC,
                staleness_status=STALENESS_FRESH,
                # Slightly lower confidence without docstring
                base_generator_score=0.85 if sym.get("docstring") else 0.65,
            )
            hint = ReferencedContextHint(
                id=hint_id,
                kind=KIND_SYMBOL_HINT,
                title=f"{sym_type}: {name}",
                summary=summary[:800],
                source_refs=[source_ref],
                generator=GeneratorMetadata(kind=GEN_DETERMINISTIC, tool_version=_TOOL_VERSION),
                confidence=ConfidenceMetadata(score=confidence_score),
                validity=ValidityMetadata(
                    scope="symbol",
                    valid_for_tasks=["navigation", "explanation", "context_selection"],
                    not_valid_for=["authoritative_code_review", "security_claim_without_original_source"],
                ),
                hashes=HashMetadata(source_hash=src_hash, summary_hash=hash_text(summary)),
                policy=HintPolicyMetadata(
                    external_cloud_safe=True,
                    sensitivity=self._estimate_sensitivity(rel_path, summary),
                ),
            )
            try:
                validate_hint(hint)
                hints.append(hint)
            except HintValidationError:
                pass
        return hints

    def generate_test_hint(
        self,
        test_file_path: str | Path,
        *,
        target_path: str | None = None,
        workspace_root: str | Path | None = None,
    ) -> ReferencedContextHint | None:
        fp = Path(test_file_path)
        if not fp.exists():
            return None
        content = self._safe_read(fp)
        if not content:
            return None

        src_hash = hash_file(str(fp))
        rel_path = str(fp.relative_to(workspace_root)) if workspace_root else str(fp)
        tests = self._count_tests(content)
        summary = (
            f"Test file `{fp.name}` — {tests} test function(s) found."
        )
        if target_path:
            summary += f" Covers: {target_path}."

        hint_id = make_hint_id(rel_path, KIND_TEST_HINT)
        source_refs = [SourceRef(path=rel_path, sha256=src_hash, role="test")]
        if target_path:
            tpath = str(Path(target_path).relative_to(workspace_root)) if workspace_root else target_path
            target_hash = hash_file(target_path)
            source_refs.append(SourceRef(path=tpath, sha256=target_hash, role="supporting"))

        confidence_score = compute_confidence(
            source_ref_count=len(source_refs),
            generator_kind=GEN_DETERMINISTIC,
            staleness_status=STALENESS_FRESH,
        )
        hint = ReferencedContextHint(
            id=hint_id,
            kind=KIND_TEST_HINT,
            title=f"Test coverage: {fp.name}",
            summary=summary,
            source_refs=source_refs,
            generator=GeneratorMetadata(kind=GEN_DETERMINISTIC, tool_version=_TOOL_VERSION),
            confidence=ConfidenceMetadata(score=confidence_score),
            validity=ValidityMetadata(
                scope="test",
                valid_for_tasks=["navigation", "explanation", "context_selection"],
                not_valid_for=["authoritative_code_review", "security_claim_without_original_source"],
            ),
            hashes=HashMetadata(source_hash=src_hash, summary_hash=hash_text(summary)),
            policy=HintPolicyMetadata(external_cloud_safe=True, sensitivity="low"),
        )
        validate_hint(hint)
        return hint

    # ── Static analysis helpers ───────────────────────────────────────────────

    def _safe_read(self, fp: Path) -> str:
        try:
            return fp.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            return ""

    def _summarize_file(self, fp: Path, content: str) -> str:
        lines = content.splitlines()
        first_lines = "\n".join(lines[:5]).strip()
        parts: list[str] = []

        if fp.suffix == ".py":
            symbols = self._extract_python_symbols(content, 8)
            class_names = [s["name"] for s in symbols if s["type"] == "class"]
            func_names = [s["name"] for s in symbols if s["type"] in ("function", "method")]
            if class_names:
                parts.append(f"Classes: {', '.join(class_names[:5])}")
            if func_names:
                parts.append(f"Functions: {', '.join(func_names[:8])}")
            imports = self._extract_imports(content)
            if imports:
                parts.append(f"Imports: {', '.join(imports[:6])}")
        elif fp.suffix in (".ts", ".js"):
            exports = [l.strip() for l in lines if "export" in l and len(l) < 120][:5]
            if exports:
                parts.append("Exports: " + "; ".join(e[:80] for e in exports[:3]))
        elif fp.suffix in (".md", ".rst"):
            headings = [l.strip("# ").strip() for l in lines if l.startswith("#")][:4]
            if headings:
                parts.append("Sections: " + " | ".join(headings))

        if not parts:
            parts.append(first_lines[:200])

        return " | ".join(parts)

    def _extract_python_symbols(self, content: str, max_symbols: int) -> list[dict]:
        symbols: list[dict] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or ""
                symbols.append({
                    "name": node.name,
                    "type": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line_start": node.lineno,
                    "line_end": getattr(node, "end_lineno", None),
                    "docstring": doc[:400] if doc else "",
                })
            if len(symbols) >= max_symbols:
                break
        return symbols

    def _extract_imports(self, content: str) -> list[str]:
        imports: list[str] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        return list(dict.fromkeys(imports))[:10]

    def _count_tests(self, content: str) -> int:
        count = 0
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return content.count("def test_")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_") or node.name.startswith("Test"):
                    count += 1
        return count

    def _estimate_sensitivity(self, path: str, content: str) -> str:
        _SENSITIVE_PATTERNS = (
            "secret", "password", "token", "auth", "security", "credential",
            "private_key", "api_key", "vault", "payment", "billing",
        )
        path_lower = path.lower()
        content_lower = (content or "")[:500].lower()
        for p in _SENSITIVE_PATTERNS:
            if p in path_lower or p in content_lower:
                return "medium"
        return "low"


# ── Manual hint importer ──────────────────────────────────────────────────────

class ManualHintImporter:
    """Import manually authored hint dicts.

    Parameters
    ----------
    owner:
        Name or email of the person creating the hints (for audit trail).
    require_source_refs:
        If True (default), reject manual hints without source_refs.
        Set False to allow scope-only notes (with domain validity instead).
    """

    def __init__(self, owner: str = "", *, require_source_refs: bool = True) -> None:
        self._owner = str(owner or "")
        self._require_refs = require_source_refs

    def import_hint(self, raw: dict[str, Any]) -> ReferencedContextHint:
        hint = ReferencedContextHint.from_dict(raw)
        hint.generator.kind = GEN_MANUAL
        hint.generator.tool_version = _TOOL_VERSION
        if self._owner and "owner" not in hint.hashes.to_dict():
            # Store owner in context_hash as annotation (not a real hash)
            hint.hashes.context_hash = hash_text(self._owner + hint.id)

        if self._require_refs and not hint.source_refs and not hint.codecompass_refs:
            validity_scope = hint.validity.scope
            if validity_scope not in ("domain", "architecture"):
                raise HintValidationError(
                    f"Manual hint '{hint.id}' has no source_refs or codecompass_refs. "
                    "Add scope='domain' or scope='architecture' to allow scope-only notes."
                )
        # Ensure manual notes are marked as not requiring human review only if they have refs
        if hint.source_refs or hint.codecompass_refs:
            hint.confidence.requires_human_review = False
        else:
            hint.confidence.requires_human_review = True

        hint.confidence.score = compute_confidence(
            source_ref_count=len(hint.source_refs),
            generator_kind=GEN_MANUAL,
            staleness_status=hint.staleness_status,
        )
        validate_hint(hint)
        return hint

    def import_many(self, raws: list[dict[str, Any]]) -> tuple[list[ReferencedContextHint], list[str]]:
        hints: list[ReferencedContextHint] = []
        errors: list[str] = []
        for i, raw in enumerate(raws):
            try:
                hints.append(self.import_hint(raw))
            except (HintValidationError, Exception) as exc:
                errors.append(f"hint[{i}]: {exc}")
        return hints, errors


# ── LLM hint generator (optional, policy-gated) ───────────────────────────────

class LlmHintGenerator:
    """Generate hints via the configured LLM.

    Only runs if ``policy_allows_llm=True`` and a generate function is provided.
    The result is marked ``generator.kind = "llm"`` and always gets
    ``requires_human_review=True`` when confidence < 0.70.
    """

    def __init__(
        self,
        *,
        generate_fn: Any | None = None,  # callable(prompt: str) → str
        model_id: str = "",
        policy_allows_llm: bool = False,
    ) -> None:
        self._generate = generate_fn
        self._model_id = model_id
        self._allowed = policy_allows_llm

    def generate_file_summary(
        self,
        file_path: str | Path,
        *,
        workspace_root: str | Path | None = None,
        hint_id_override: str | None = None,
    ) -> ReferencedContextHint | None:
        if not self._allowed or self._generate is None:
            return None
        fp = Path(file_path)
        if not fp.exists():
            return None
        content = fp.read_text(encoding="utf-8", errors="replace")[:4000]
        src_hash = hash_file(str(fp))
        rel_path = str(fp.relative_to(workspace_root)) if workspace_root else str(fp)

        prompt = (
            f"Summarize the purpose of the following file for use as a code hint. "
            f"Be concise (2-3 sentences). Do not quote code verbatim. "
            f"File: {rel_path}\n\n{content}\n\nSummary:"
        )
        try:
            summary = str(self._generate(prompt) or "").strip()
        except Exception as exc:
            return None

        if not summary:
            return None

        prompt_hash = hash_text(prompt)
        hint_id = hint_id_override or make_hint_id(rel_path, KIND_FILE_SUMMARY, extra="llm")
        confidence_score = compute_confidence(
            source_ref_count=1,
            generator_kind=GEN_LLM,
            staleness_status=STALENESS_FRESH,
        )
        hint = ReferencedContextHint(
            id=hint_id,
            kind=KIND_FILE_SUMMARY,
            title=f"LLM summary: {fp.name}",
            summary=summary[:1000],
            source_refs=[SourceRef(path=rel_path, sha256=src_hash, role="primary_source")],
            generator=GeneratorMetadata(
                kind=GEN_LLM,
                model_id=self._model_id or None,
                prompt_hash=prompt_hash,
                tool_version=_TOOL_VERSION,
            ),
            confidence=ConfidenceMetadata(
                score=confidence_score,
                method="llm_output+source_ref_coverage",
                requires_human_review=confidence_score < 0.70,
            ),
            validity=ValidityMetadata(
                scope="file",
                valid_for_tasks=["explanation", "context_selection"],
                not_valid_for=[
                    "authoritative_code_review",
                    "security_claim_without_original_source",
                ],
            ),
            hashes=HashMetadata(
                source_hash=src_hash,
                summary_hash=hash_text(summary),
                context_hash=hash_text(rel_path + prompt_hash),
            ),
            policy=HintPolicyMetadata(
                external_cloud_safe=False,
                contains_original_code_excerpt=False,
                sensitivity="unknown",
            ),
        )
        validate_hint(hint)
        return hint


# ── Restricted inference hint generator ───────────────────────────────────────

class RestrictedInferenceHintGenerator:
    """Use RestrictedModelInferenceService for label/score-based hints.

    Does NOT produce free text — only classification labels, confidence
    scores and domain tags.  Respects path policy (blocked → return None).
    """

    def __init__(
        self,
        *,
        inference_service: Any | None = None,  # RestrictedModelInferenceService
    ) -> None:
        self._svc = inference_service

    def classify_domain(
        self,
        file_path: str | Path,
        *,
        workspace_root: str | Path | None = None,
        domain_labels: list[str] | None = None,
    ) -> ReferencedContextHint | None:
        if self._svc is None:
            return None
        fp = Path(file_path)
        if not fp.exists():
            return None

        labels = domain_labels or ["auth", "billing", "rag", "security", "config",
                                    "worker", "testing", "frontend", "api", "db"]
        rel_path = str(fp.relative_to(workspace_root)) if workspace_root else str(fp)
        src_hash = hash_file(str(fp))

        try:
            choices = self._svc.score_choices(
                path=rel_path,
                prompt=f"File: {fp.name}",
                choices=labels,
            )
            if not choices:
                return None
            top = max(choices, key=lambda c: getattr(c, "score", 0))
            label = str(getattr(top, "choice", "unknown"))
            score = float(getattr(top, "score", 0.5))
        except Exception:
            return None

        hint_id = make_hint_id(rel_path, KIND_DOMAIN_SUMMARY, extra="rti_classify")
        summary = f"Domain classification (restricted inference): {label} (score {score:.2f})"
        confidence_score = compute_confidence(
            source_ref_count=1,
            generator_kind=GEN_RESTRICTED_TRANSFORMER_INFERENCE,
            staleness_status=STALENESS_FRESH,
            base_generator_score=score,
        )
        hint = ReferencedContextHint(
            id=hint_id,
            kind=KIND_DOMAIN_SUMMARY,
            title=f"Domain: {label} — {fp.name}",
            summary=summary,
            source_refs=[SourceRef(path=rel_path, sha256=src_hash, role="primary_source")],
            generator=GeneratorMetadata(
                kind=GEN_RESTRICTED_TRANSFORMER_INFERENCE,
                tool_version=_TOOL_VERSION,
            ),
            confidence=ConfidenceMetadata(
                score=confidence_score,
                method="restricted_inference_classification",
                requires_human_review=score < 0.60,
            ),
            validity=ValidityMetadata(
                scope="domain",
                valid_for_tasks=["navigation", "context_selection"],
                not_valid_for=["authoritative_code_review", "security_claim_without_original_source"],
            ),
            hashes=HashMetadata(
                source_hash=src_hash,
                summary_hash=hash_text(summary),
            ),
            policy=HintPolicyMetadata(
                external_cloud_safe=True,
                contains_original_code_excerpt=False,
                sensitivity="low",
            ),
        )
        validate_hint(hint)
        return hint


# ── Hybrid generator ──────────────────────────────────────────────────────────

class HybridHintGenerator:
    """Combine DeterministicHintGenerator + optional LLM enrichment.

    Strategy:
    1. Run deterministic generator for a base summary.
    2. If LLM is allowed and available, augment the summary (not replace it).
    3. Mark result as generator.kind='hybrid'.
    """

    def __init__(
        self,
        *,
        deterministic: DeterministicHintGenerator | None = None,
        llm: LlmHintGenerator | None = None,
    ) -> None:
        self._det = deterministic or DeterministicHintGenerator()
        self._llm = llm

    def generate_file_summary(
        self,
        file_path: str | Path,
        *,
        workspace_root: str | Path | None = None,
    ) -> ReferencedContextHint | None:
        base = self._det.generate_file_summary(file_path, workspace_root=workspace_root)
        if base is None:
            return None

        llm_hint = None
        if self._llm is not None:
            llm_hint = self._llm.generate_file_summary(
                file_path,
                workspace_root=workspace_root,
                hint_id_override=base.id + ":llm",
            )

        if llm_hint is not None:
            combined_summary = (
                f"{base.summary} | LLM: {llm_hint.summary}"
            )
            base.summary = combined_summary[:1200]
            base.generator = GeneratorMetadata(
                kind=GEN_HYBRID,
                model_id=llm_hint.generator.model_id,
                prompt_hash=llm_hint.generator.prompt_hash,
                tool_version=_TOOL_VERSION,
            )
            base.hashes = HashMetadata(
                source_hash=base.hashes.source_hash,
                summary_hash=hash_text(combined_summary),
                context_hash=base.hashes.context_hash,
            )
            base.confidence.score = compute_confidence(
                source_ref_count=len(base.source_refs),
                generator_kind=GEN_HYBRID,
                staleness_status=STALENESS_FRESH,
            )

        return base
