"""RCHCS-004/005/006: Main coordinator for Referenced Context Hints.

Ties together the store, generators, staleness enforcement, and
integration hooks for CodeCompass ContextPackage and PromptBuilder.

Usage
-----
svc = get_referenced_context_hint_service()

# Get hints for candidates from CodeCompass resolve_context
hints = svc.hints_for_candidates(candidate_files)

# Generate and store a file summary
hint = svc.generate_and_store(
    file_path="agent/services/foo.py",
    kind=KIND_FILE_SUMMARY,
    generator_type="deterministic",
)

# Invalidate when files change
svc.on_files_changed(["agent/services/foo.py"])
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.services.referenced_context_hint_schema import (
    KIND_FILE_SUMMARY,
    KIND_SYMBOL_HINT,
    KIND_TEST_HINT,
    STALENESS_INVALID,
    STALENESS_STALE,
    ReferencedContextHint,
    compute_staleness,
    hash_file,
)
from agent.services.referenced_context_hint_store import (
    ReferencedContextHintStore,
    get_referenced_context_hint_store,
)
from agent.services.referenced_context_hint_generators import (
    DeterministicHintGenerator,
    HybridHintGenerator,
    LlmHintGenerator,
    ManualHintImporter,
    RestrictedInferenceHintGenerator,
)


class ReferencedContextHintService:
    """Orchestrates hint generation, staleness, and context integration.

    Parameters
    ----------
    store:
        ReferencedContextHintStore to use.
    deterministic_generator:
        Injected DeterministicHintGenerator (or None → creates default).
    workspace_root:
        Repo root for relative-path computation.
    max_hints_per_package:
        Hard cap on derived_context_hints per ContextPackage.
    """

    def __init__(
        self,
        *,
        store: ReferencedContextHintStore | None = None,
        deterministic_generator: DeterministicHintGenerator | None = None,
        inference_service: Any | None = None,
        workspace_root: str | Path | None = None,
        max_hints_per_package: int = 10,
        current_manifest_hash: str = "",
    ) -> None:
        self._store = store or get_referenced_context_hint_store()
        self._det = deterministic_generator or DeterministicHintGenerator()
        self._rti = RestrictedInferenceHintGenerator(inference_service=inference_service)
        self._root = Path(workspace_root or ".").resolve()
        self._max = max_hints_per_package
        self._manifest_hash = current_manifest_hash

    # ── Context package integration ───────────────────────────────────────────

    def hints_for_candidates(
        self,
        candidate_files: list[dict[str, Any]],
        *,
        task: str = "context_selection",
        generate_missing: bool = False,
    ) -> list[dict[str, Any]]:
        """Return serialised hints for a list of CodeCompass candidate_files.

        Includes only fresh/possibly_stale hints that are valid for ``task``.
        Generates deterministic hints on the fly if ``generate_missing=True``.
        """
        paths = [str(c.get("path") or "") for c in candidate_files if c.get("path")]
        hints = self._store.search_by_paths(paths, limit=self._max * 3)

        # Update staleness in-memory before filtering
        fresh_hints: list[ReferencedContextHint] = []
        for h in hints:
            status = self._recheck_staleness(h)
            if status in (STALENESS_STALE, STALENESS_INVALID):
                continue
            if not h.is_safe_for_task(task):
                continue
            fresh_hints.append(h)

        # Generate missing if requested
        if generate_missing:
            covered = {r.path for h in fresh_hints for r in h.source_refs}
            for path in paths:
                if path not in covered:
                    generated = self._try_generate(path, KIND_FILE_SUMMARY)
                    if generated:
                        try:
                            self._store.put(generated)
                            if generated.is_safe_for_task(task):
                                fresh_hints.append(generated)
                        except Exception:
                            pass

        fresh_hints.sort(key=lambda h: -h.confidence.score)
        return [self._to_context_dict(h) for h in fresh_hints[: self._max]]

    def generate_and_store(
        self,
        file_path: str | Path,
        *,
        kind: str = KIND_FILE_SUMMARY,
        generator_type: str = "deterministic",
    ) -> ReferencedContextHint | None:
        """Generate a hint for ``file_path`` and store it."""
        hint = self._try_generate(str(file_path), kind, generator_type=generator_type)
        if hint is None:
            return None
        try:
            self._store.put(hint)
        except Exception:
            pass
        return hint

    def import_manual_hints(
        self,
        raws: list[dict[str, Any]],
        *,
        owner: str = "",
        require_source_refs: bool = True,
    ) -> tuple[list[ReferencedContextHint], list[str]]:
        """Import manual hints; returns (accepted, errors)."""
        importer = ManualHintImporter(owner=owner, require_source_refs=require_source_refs)
        hints, errors = importer.import_many(raws)
        for h in hints:
            try:
                self._store.put(h)
            except Exception as exc:
                errors.append(f"{h.id}: {exc}")
        return hints, errors

    def on_files_changed(
        self, changed_paths: list[str], *, current_manifest_hash: str = ""
    ) -> list[str]:
        """Invalidate stale hints for changed paths. Returns affected hint IDs.

        Accepts both absolute and workspace-relative paths and normalises them
        so store hits are found regardless of which form was stored.
        """
        mh = current_manifest_hash or self._manifest_hash
        normalised: list[str] = []
        for p in changed_paths:
            normalised.append(p)  # as-is
            ap = Path(p)
            if ap.is_absolute():
                try:
                    normalised.append(str(ap.relative_to(self._root)))
                except ValueError:
                    pass
            else:
                normalised.append(str(self._root / p))
        return self._store.invalidate_stale_for_paths(
            list(dict.fromkeys(normalised)), current_manifest_hash=mh
        )

    def get_hints(
        self,
        *,
        path: str | None = None,
        kind: str | None = None,
        domain: str | None = None,
        task: str = "context_selection",
        limit: int = 20,
    ) -> list[ReferencedContextHint]:
        """Retrieve hints filtered by path, kind, domain, and task validity."""
        hints = self._store.search(path=path, kind=kind, domain=domain, limit=limit * 2)
        valid: list[ReferencedContextHint] = []
        for h in hints:
            status = self._recheck_staleness(h)
            if status in (STALENESS_STALE, STALENESS_INVALID):
                continue
            if h.is_safe_for_task(task):
                valid.append(h)
        valid.sort(key=lambda h: -h.confidence.score)
        return valid[:limit]

    def stats(self) -> dict[str, Any]:
        return self._store.stats()

    # ── Prompt builder helpers ────────────────────────────────────────────────

    def build_hints_prompt_section(
        self,
        hints: list[dict[str, Any]] | list[ReferencedContextHint],
        *,
        max_chars: int = 1200,
    ) -> str:
        """Format hints as a prompt section marked as derived/referenced info.

        The prompt clearly labels each hint as derived, non-authoritative,
        and names its source file to enable evidence tracing.
        """
        lines: list[str] = [
            "[Abgeleitete Code-Hints — referenziert, nicht autoritativ]",
            "(Diese Informationen stammen aus Analysen und Zusammenfassungen, nicht aus Original-Quellcode.)",
        ]
        total = len("\n".join(lines))

        hint_dicts: list[dict[str, Any]] = []
        for h in hints:
            if isinstance(h, ReferencedContextHint):
                hint_dicts.append(self._to_context_dict(h))
            else:
                hint_dicts.append(dict(h))

        for hd in hint_dicts:
            refs = ", ".join(hd.get("source_paths") or [])
            gen = str(hd.get("generator_kind") or "?")
            staleness = str(hd.get("staleness_status") or "")
            title = str(hd.get("title") or "")
            summary = str(hd.get("summary") or "")
            staleness_note = f" [{staleness}]" if staleness != "fresh" else ""
            line = f"• [{gen}]{staleness_note} {title} (Refs: {refs})\n  {summary}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)

        return "\n".join(lines) if len(lines) > 2 else ""

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _try_generate(
        self,
        file_path: str,
        kind: str,
        *,
        generator_type: str = "deterministic",
    ) -> ReferencedContextHint | None:
        fp = Path(file_path)
        if not fp.exists():
            fp = self._root / file_path
        if not fp.exists():
            return None

        if kind == KIND_FILE_SUMMARY:
            if generator_type == "llm":
                return None  # LLM gen requires external setup
            return self._det.generate_file_summary(fp, workspace_root=self._root)
        elif kind == KIND_SYMBOL_HINT:
            hints = self._det.generate_symbol_hints(fp, workspace_root=self._root)
            return hints[0] if hints else None
        elif kind == KIND_TEST_HINT:
            return self._det.generate_test_hint(fp, workspace_root=self._root)
        return None

    def _recheck_staleness(self, hint: ReferencedContextHint) -> str:
        if not hint.source_refs:
            return hint.staleness_status
        primary = hint.source_refs[0]
        abs_path = (self._root / primary.path if not Path(primary.path).is_absolute()
                    else Path(primary.path))
        if not abs_path.exists():
            # Can only confirm file gone when workspace root is a real project root
            # (not ".") and we previously had a hash for this file.
            stored_hash = hint.hashes.source_hash or primary.sha256
            real_root = self._root.resolve() != Path.cwd()
            if stored_hash and real_root:
                hint.staleness_status = STALENESS_INVALID
                return STALENESS_INVALID
            return hint.staleness_status
        cur_hash = hash_file(str(abs_path))
        new_status = compute_staleness(
            hint,
            current_source_hash=cur_hash,
            current_manifest_hash=self._manifest_hash,
            source_exists=True,
        )
        if new_status != hint.staleness_status:
            hint.staleness_status = new_status
        return new_status

    def _to_context_dict(self, hint: ReferencedContextHint) -> dict[str, Any]:
        return {
            "hint_id": hint.id,
            "kind": hint.kind,
            "title": hint.title,
            "summary": hint.summary,
            "source_paths": [r.path for r in hint.source_refs],
            "generator_kind": hint.generator.kind,
            "confidence": hint.confidence.score,
            "requires_human_review": hint.confidence.requires_human_review,
            "staleness_status": hint.staleness_status,
            "valid_for_tasks": hint.validity.valid_for_tasks,
            "not_valid_for": hint.validity.not_valid_for,
            "external_cloud_safe": hint.policy.external_cloud_safe,
            "sensitivity": hint.policy.sensitivity,
        }


# ── Module singleton ──────────────────────────────────────────────────────────

_svc_instance: ReferencedContextHintService | None = None


def get_referenced_context_hint_service(
    *,
    store: ReferencedContextHintStore | None = None,
    workspace_root: str | Path | None = None,
    current_manifest_hash: str = "",
) -> ReferencedContextHintService:
    global _svc_instance
    if _svc_instance is None:
        _svc_instance = ReferencedContextHintService(
            store=store,
            workspace_root=workspace_root,
            current_manifest_hash=current_manifest_hash,
        )
    return _svc_instance


def reset_referenced_context_hint_service(new: ReferencedContextHintService | None = None) -> None:
    global _svc_instance
    _svc_instance = new
