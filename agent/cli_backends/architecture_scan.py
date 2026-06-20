from __future__ import annotations

import hashlib
import json
import logging
import pathlib
from typing import Any

from flask import has_app_context

from agent.config import settings
from agent.cli_backends.helpers import _get_agent_config
from agent.cli_backends.context import default_context as _ctx

log = logging.getLogger(__name__)

_EXT_LANG: dict[str, str] = {
    "py": "python", "ts": "typescript", "tsx": "typescript",
    "js": "javascript", "jsx": "javascript",
    "yaml": "yaml", "yml": "yaml", "json": "json",
    "md": "markdown", "html": "html", "css": "css",
    "sh": "bash", "bash": "bash",
}

# CCSH-004: Accepted alias names for line-range and snippet fields
_LR_START_ALIASES: tuple[str, ...] = ("start_line", "line_start", "start", "from_line")
_LR_END_ALIASES: tuple[str, ...] = ("end_line", "line_end", "end", "to_line")
_SNIPPET_FIELD_ALIASES: tuple[str, ...] = ("snippet", "content", "excerpt")

_MAX_LINE_SPAN: int = 5000
_MAX_LINE_WINDOW: int = 200


def _get_ref_alias(ref: dict, aliases: tuple[str, ...]) -> object:
    for k in aliases:
        v = ref.get(k)
        if v is not None:
            return v
    return None


def _normalize_line_range(ref: dict) -> "tuple[int, int] | None":
    start = _get_ref_alias(ref, _LR_START_ALIASES)
    end = _get_ref_alias(ref, _LR_END_ALIASES)
    if start is None or end is None:
        return None
    try:
        s, e = int(start), int(end)
    except (TypeError, ValueError):
        return None
    if s < 1 or e < s or (e - s) > _MAX_LINE_SPAN:
        return None
    return (s, e)


def _read_line_window(
    full_path: pathlib.Path,
    start: int,
    end: int,
    context_lines: int,
    per_file_chars: int,
) -> "tuple[str, int, int]":
    """Read lines [start..end] + context_lines margin from file (1-indexed)."""
    try:
        raw = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0, 0
    lines = raw.splitlines()
    total = len(lines)
    if total == 0:
        return "", 0, 0
    context_lines = max(0, min(context_lines, _MAX_LINE_WINDOW))
    lo = max(0, start - 1 - context_lines)
    hi = min(total, end + context_lines)
    excerpt = "\n".join(lines[lo:hi])
    if len(excerpt) > per_file_chars:
        excerpt = excerpt[:per_file_chars].rstrip() + "\n# [… gekürzt]"
    return excerpt, lo + 1, min(hi, total)


def _get_worker_context_cfg() -> dict:
    agent_cfg = _get_agent_config() if has_app_context() else {}
    ctx_cfg = dict(agent_cfg.get("ananta_worker_context") or {})
    return ctx_cfg


def _bounded_worker_int(key: str, default: int, lo: int, hi: int) -> int:
    ctx_cfg = _get_worker_context_cfg()
    raw = ctx_cfg.get(key)
    if raw is not None:
        try:
            return max(lo, min(hi, int(raw)))
        except (TypeError, ValueError):
            pass
    return max(lo, min(hi, getattr(settings, key, default)))


def _resolve_repo_root() -> pathlib.Path | None:
    """Return the configured project/repo root generically via settings.rag_repo_root."""
    if has_app_context():
        raw = str(getattr(settings, "rag_repo_root", "") or "").strip()
        if raw and raw != ".":
            p = pathlib.Path(raw)
            if p.is_dir():
                return p.resolve()
    for candidate in (pathlib.Path("/app"), pathlib.Path.cwd()):
        if candidate.is_dir() and (candidate / "agent").is_dir():
            return candidate.resolve()
    return None


def _load_source_file_batches(
    workdir: str | None,
    *,
    files_per_batch: int = 3,
    per_file_chars: int = 4_000,
    max_files: int = 30,
    context_lines: int = 5,
    max_snippet_chars: int = 8_000,
) -> "list[list[dict]]":
    """Load relevant source files from workspace and split into batches."""
    batches: list[list[dict]] = []
    if not workdir:
        return batches
    root = pathlib.Path(workdir)
    if not root.is_dir():
        return batches

    repo_root = _resolve_repo_root()
    research_json = root / "rag_helper" / "research-context.json"

    blocks: list[dict] = []
    seen_keys: set[str] = set()

    def _dedup_key(rel: str, s: "int | None", e: "int | None", content: str = "") -> str:
        if s is None and e is None and content:
            suffix = hashlib.md5(content[:200].encode(), usedforsecurity=False).hexdigest()[:8]
            return f"{rel}:h:{suffix}"
        return f"{rel}:{s}:{e}"

    if research_json.exists() and repo_root is not None:
        try:
            data = json.loads(research_json.read_text(encoding="utf-8", errors="replace"))
            profile = dict(data.get("retrieval_profile") or {})
            full_scan = str(profile.get("analysis_mode") or data.get("analysis_mode") or "").strip() == "architecture_full_scan"
            architecture_scope = dict(data.get("architecture_scope") or {})
            raw_refs = architecture_scope.get("refs") if full_scan and architecture_scope.get("refs") else data.get("repo_scope_refs")
            refs = [dict(r or {}) for r in list(raw_refs or []) if r]
            resolved_root = repo_root.resolve()

            for ref in refs:
                rel_path = str(ref.get("path") or "").strip()
                score_raw = ref.get("score")
                score = float(score_raw) if score_raw is not None else None
                reason = str(ref.get("reason") or "").strip() or None
                symbol = str(ref.get("symbol") or "").strip() or None
                snippet_raw = _get_ref_alias(ref, _SNIPPET_FIELD_ALIASES)
                line_range = _normalize_line_range(ref)

                full: pathlib.Path | None = None
                if rel_path:
                    try:
                        candidate = (repo_root / rel_path).resolve()
                        candidate.relative_to(resolved_root)
                        if candidate.is_file():
                            full = candidate
                    except (ValueError, OSError):
                        pass

                # Priority 1: path + line-range → read window from current file
                if full is not None and line_range is not None:
                    content, actual_start, actual_end = _read_line_window(
                        full, line_range[0], line_range[1], context_lines, per_file_chars
                    )
                    if content:
                        dk = _dedup_key(rel_path, actual_start, actual_end)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            lang = _EXT_LANG.get(full.suffix.lstrip("."), full.suffix.lstrip(".") or "text")
                            blocks.append({
                                "rel_path": rel_path,
                                "lang": lang,
                                "content": content,
                                "source_kind": "line_range",
                                "start_line": actual_start,
                                "end_line": actual_end,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
                        continue

                # Priority 2: ref.chunks[] — use embedded chunk content
                ref_chunks = [dict(c or {}) for c in list(ref.get("chunks") or []) if c]
                if ref_chunks:
                    for chunk in ref_chunks:
                        chunk_content = str(chunk.get("content") or chunk.get("excerpt") or "").strip()
                        if not chunk_content:
                            continue
                        chunk_source = str(chunk.get("source") or rel_path or "").strip()
                        chunk_meta = dict(chunk.get("metadata") or {})
                        c_start = chunk_meta.get("start_line")
                        c_end = chunk_meta.get("end_line")
                        try:
                            c_start = int(c_start) if c_start is not None else None
                            c_end = int(c_end) if c_end is not None else None
                        except (TypeError, ValueError):
                            c_start = c_end = None
                        dk = _dedup_key(chunk_source, c_start, c_end, chunk_content)
                        if dk in seen_keys:
                            continue
                        seen_keys.add(dk)
                        ext = pathlib.Path(chunk_source).suffix.lstrip(".")
                        lang = _EXT_LANG.get(ext, ext or "text")
                        c_score_raw = chunk.get("score")
                        c_score = float(c_score_raw) if c_score_raw is not None else score
                        chunk_content_clipped = chunk_content[:per_file_chars]
                        if len(chunk_content) > per_file_chars:
                            chunk_content_clipped = chunk_content_clipped.rstrip() + "\n# [… gekürzt]"
                        blocks.append({
                            "rel_path": chunk_source,
                            "lang": lang,
                            "content": chunk_content_clipped,
                            "source_kind": "chunk",
                            "start_line": c_start,
                            "end_line": c_end,
                            "score": c_score,
                            "reason": reason,
                            "symbol": symbol,
                        })
                    continue

                # Priority 3: path only → file beginning (legacy fallback)
                if full is not None:
                    try:
                        raw = full.read_text(encoding="utf-8", errors="replace").strip()
                    except OSError:
                        raw = ""
                    if raw:
                        dk = _dedup_key(rel_path, None, None)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            content = raw[:per_file_chars]
                            if len(raw) > per_file_chars:
                                content = content.rstrip() + "\n# [… gekürzt]"
                            lang = _EXT_LANG.get(full.suffix.lstrip("."), full.suffix.lstrip(".") or "text")
                            blocks.append({
                                "rel_path": rel_path,
                                "lang": lang,
                                "content": content,
                                "source_kind": "file_excerpt",
                                "start_line": None,
                                "end_line": None,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
                        continue

                # Priority 4: snippet without valid path
                if snippet_raw:
                    snippet_text = str(snippet_raw).strip()[:max_snippet_chars]
                    if snippet_text:
                        s_start = line_range[0] if line_range else None
                        s_end = line_range[1] if line_range else None
                        dk = _dedup_key(rel_path or "(snippet)", s_start, s_end)
                        if dk not in seen_keys:
                            seen_keys.add(dk)
                            ext = pathlib.Path(rel_path).suffix.lstrip(".") if rel_path else ""
                            lang = _EXT_LANG.get(ext, ext or "text")
                            blocks.append({
                                "rel_path": rel_path or "(codecompass_snippet)",
                                "lang": lang,
                                "content": snippet_text,
                                "source_kind": "codecompass_snippet",
                                "start_line": s_start,
                                "end_line": s_end,
                                "score": score,
                                "reason": reason,
                                "symbol": symbol,
                            })
        except Exception:
            pass

    blocks.sort(key=lambda b: -(b["score"] or 0.0))

    if len(blocks) > max_files:
        omitted = len(blocks) - max_files
        log.debug(
            "ananta-worker context budget: keeping top %s/%s blocks, omitting %s lower-scored",
            max_files, len(blocks), omitted,
        )
        blocks = blocks[:max_files]

    for i in range(0, len(blocks), files_per_batch):
        batches.append(blocks[i : i + files_per_batch])

    # Priority 5: hub-context.md fallback when nothing else loaded
    if not batches:
        hub_path = root / ".ananta" / "hub-context.md"
        if hub_path.exists():
            try:
                content = hub_path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    batches.append([{
                        "rel_path": "hub-context.md",
                        "lang": "markdown",
                        "content": content[:12_000],
                        "source_kind": "hub_context",
                        "start_line": None,
                        "end_line": None,
                        "score": None,
                        "reason": None,
                        "symbol": None,
                    }])
            except OSError:
                pass

    return batches


def _format_block_header(block: dict) -> str:
    """Build the ### header for a context block (CCSH-002)."""
    rel_path = block.get("rel_path") or ""
    source_kind = block.get("source_kind") or "file_excerpt"
    start_line = block.get("start_line")
    end_line = block.get("end_line")
    score = block.get("score")
    symbol = block.get("symbol")

    if start_line is not None and end_line is not None:
        location = f"{rel_path}:{start_line}-{end_line}"
    else:
        location = rel_path

    tag_parts = [source_kind]
    if symbol:
        tag_parts.append(f"symbol={symbol}")
    if score is not None:
        tag_parts.append(f"score={score:.2f}")
    tag = " ".join(tag_parts)
    return f"### {location} [{tag}]"


# CCARI-005: canonical runtime rule for the ananta-worker iteration prompt.
# Inserted as a one-line preamble whenever the prompt contains
# ``codecompass_snippet`` blocks. See
# ``docs/codecompass-agent-runtime-instructions.md`` for the full doc and
# ``docs/security/codecompass-context-trust-model.md`` for the trust model.
_CODECOMPASS_RUNTIME_RULE = (
    "**CodeCompass runtime rule:** Behandle die unten geladenen CodeCompass-"
    "Snippets als indexierte Repo-Hinweise mit Evidence, nicht als Wahrheit. "
    "Wenn relevante Daten fehlen, benenne den fehlenden Kontext und fordere "
    "gezielt ueber den Hub Nachladen an. Behaupte keine Coverage, Policy-"
    "Wirkung oder Dependency ohne Evidence-Pfad."
)


def _needs_codecompass_runtime_rules(batch: "list[dict]") -> bool:
    """CCARI-005: True iff the iteration batch contains at least one
    ``codecompass_snippet`` block. Pure observation, never raises."""
    if not batch:
        return False
    for block in batch:
        if not isinstance(block, dict):
            continue
        if str(block.get("source_kind") or "").strip() == "codecompass_snippet":
            return True
    return False


def _build_iteration_prompt(
    original_prompt: str,
    *,
    batch: "list[dict]",
    progress_so_far: str,
    step: int,
    total_steps: int,
    is_synthesis: bool = False,
) -> str:
    """Assemble the prompt for one iteration step of the ananta-worker loop."""
    parts: list[str] = [original_prompt.rstrip(), "\n\n---\n\n"]

    # CCARI-005: prepend the codecompass runtime rule when at least one block in
    # the batch is a codecompass_snippet. The rule is a one-line reminder; the
    # full ruleset is documented in
    # ``docs/codecompass-agent-runtime-instructions.md``.
    if _needs_codecompass_runtime_rules(batch):
        parts.append(_CODECOMPASS_RUNTIME_RULE + "\n\n---\n\n")

    if progress_so_far:
        prog = progress_so_far if len(progress_so_far) <= 6_000 else "…\n" + progress_so_far[-6_000:]
        parts.append(f"**Bisheriger Arbeitsfortschritt:**\n\n{prog}\n\n---\n\n")

    if is_synthesis:
        parts.append(
            "Alle relevanten Quelldateien wurden analysiert. "
            "Erstelle jetzt das vollständige, abschließende Ergebnis "
            "basierend auf dem gesamten Arbeitsfortschritt oben. "
            "Antworte direkt ohne weitere Schritte anzukündigen."
        )
    else:
        if not progress_so_far:
            header = (
                f"**Schritt {step}/{total_steps}** — "
                "Analysiere die folgenden Quelldateien und halte deine "
                "Teilergebnisse und Erkenntnisse strukturiert fest. "
                "Antworte nur mit deinem Fortschritt, noch nicht dem Endergebnis."
            )
        else:
            header = (
                f"**Schritt {step}/{total_steps}** — "
                "Analysiere die weiteren Quelldateien und ergänze deinen Fortschritt."
            )
        parts.append(header + "\n\n")
        for block in batch:
            h = _format_block_header(block)
            lang = block.get("lang") or "text"
            content = block.get("content") or ""
            parts.append(f"{h}\n```{lang}\n{content}\n```\n\n")

    return "".join(parts)


def _read_research_context(workdir: str | None) -> dict:
    if not workdir:
        return {}
    path = pathlib.Path(workdir) / "rag_helper" / "research-context.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return dict(data or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_architecture_full_scan_context(ctx: dict) -> bool:
    profile = dict((ctx or {}).get("retrieval_profile") or {})
    return str(profile.get("analysis_mode") or (ctx or {}).get("analysis_mode") or "").strip() == "architecture_full_scan"


def _write_json(path: pathlib.Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def _summary_empty(plan: dict) -> dict:
    return {
        "schema": "architecture_analysis_summary.v1",
        "status": "in_progress",
        "plan_id": plan.get("plan_id"),
        "components": [],
        "edges": [],
        "entrypoints": [],
        "data_flows": [],
        "security_boundaries": [],
        "configuration_points": [],
        "runtime_paths": [],
        "open_questions": [],
        "source_evidence": [],
        "coverage": {
            "planned_refs": int((plan.get("coverage") or {}).get("planned_refs") or 0),
            "processed_refs": 0,
            "omitted_refs": int((plan.get("coverage") or {}).get("excluded_refs") or 0),
            "processed_source_kinds": {},
            "omitted_reasons": {},
        },
    }


def _extract_json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
        return dict(obj) if isinstance(obj, dict) else None
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start:end + 1])
                return dict(obj) if isinstance(obj, dict) else None
            except Exception:
                return None
    return None


def _append_unique(rows: list, incoming: list, key_fields: tuple[str, ...]) -> None:
    def _row_key(item: Any) -> tuple[str, ...]:
        row = dict(item or {}) if isinstance(item, dict) else {"value": str(item)}
        key = tuple(str(row.get(k) or "") for k in key_fields)
        if not any(key):
            key = tuple(str(row.get("value") or "") for _ in key_fields)
        return key

    seen = {_row_key(row) for row in rows}
    for item in incoming:
        row = dict(item or {}) if isinstance(item, dict) else {"value": str(item)}
        key = _row_key(row)
        if key not in seen:
            seen.add(key)
            rows.append(row)


def _merge_batch_analysis(summary: dict, batch_result: dict | None, *, batch: list[dict], raw_output: str) -> dict:
    result = dict(batch_result or {})
    if result.get("schema") != "architecture_batch_analysis.v1":
        result = {
            "schema": "architecture_batch_analysis.v1",
            "status": "degraded",
            "analyzed_refs": [
                {"path": b.get("rel_path"), "source_kind": b.get("source_kind"), "start_line": b.get("start_line"), "end_line": b.get("end_line")}
                for b in batch
            ],
            "source_evidence": [
                {"source": b.get("rel_path"), "source_kind": b.get("source_kind"), "note": str(raw_output or "")[:500]}
                for b in batch
            ],
            "unresolved_questions": ["batch_output_not_valid_json"],
        }
        summary["status"] = "degraded"

    _append_unique(summary["components"], list(result.get("components") or []), ("name", "source"))
    _append_unique(summary["edges"], list(result.get("edges") or []), ("from", "to", "relation"))
    _append_unique(summary["entrypoints"], list(result.get("entrypoints") or []), ("path", "symbol"))
    _append_unique(summary["data_flows"], list(result.get("data_flows") or []), ("from", "to", "description"))
    _append_unique(summary["security_boundaries"], list(result.get("security_notes") or result.get("security_boundaries") or []), ("source", "description"))
    _append_unique(summary["configuration_points"], list(result.get("config_notes") or result.get("configuration_points") or []), ("source", "key"))
    _append_unique(summary["runtime_paths"], list(result.get("runtime_paths") or []), ("name", "source"))
    _append_unique(summary["open_questions"], list(result.get("unresolved_questions") or result.get("open_questions") or []), ("question",))
    _append_unique(summary["source_evidence"], list(result.get("source_evidence") or []), ("source", "source_kind", "note"))

    coverage = dict(summary.get("coverage") or {})
    processed = int(coverage.get("processed_refs") or 0)
    for block in batch:
        source_kind = str(block.get("source_kind") or "unknown")
        counts = dict(coverage.get("processed_source_kinds") or {})
        counts[source_kind] = int(counts.get(source_kind) or 0) + 1
        coverage["processed_source_kinds"] = counts
    coverage["processed_refs"] = processed + len(batch)
    summary["coverage"] = coverage
    return summary


def _summary_for_prompt(summary: dict, max_chars: int) -> str:
    compact = {
        "schema": summary.get("schema"),
        "status": summary.get("status"),
        "components": list(summary.get("components") or [])[:40],
        "edges": list(summary.get("edges") or [])[:80],
        "entrypoints": list(summary.get("entrypoints") or [])[:40],
        "data_flows": list(summary.get("data_flows") or [])[:40],
        "security_boundaries": list(summary.get("security_boundaries") or [])[:30],
        "configuration_points": list(summary.get("configuration_points") or [])[:30],
        "runtime_paths": list(summary.get("runtime_paths") or [])[:30],
        "open_questions": list(summary.get("open_questions") or [])[:30],
        "source_evidence": list(summary.get("source_evidence") or [])[:120],
        "coverage": summary.get("coverage") or {},
    }
    text = json.dumps(compact, indent=2, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n..."
    return text


def _build_architecture_batch_prompt(original_prompt: str, *, batch: list[dict], summary: dict, step: int, total_steps: int, max_summary_chars: int) -> str:
    parts = [
        original_prompt.rstrip(),
        "\n\n---\n\n",
        "Du arbeitest im Architektur-Full-Scan-Modus. Antworte mit JSON, optional fenced, mit schema='architecture_batch_analysis.v1'.\n",
        "Erwartete Felder: analyzed_refs, components, edges, data_flows, security_notes, config_notes, runtime_paths, unresolved_questions, source_evidence, confidence.\n",
        "Erfinde keine konkreten Dateipfade. Nutze nur die unten aufgeführten Quellen als Evidence.\n\n",
        f"Schritt {step}/{total_steps}\n\n",
        "Aktuelle strukturierte Summary:\n",
        _summary_for_prompt(summary, max_summary_chars),
        "\n\nQuellen dieses Batches:\n\n",
    ]
    for block in batch:
        h = _format_block_header(block)
        lang = block.get("lang") or "text"
        content = block.get("content") or ""
        parts.append(f"{h}\n```{lang}\n{content}\n```\n\n")
    return "".join(parts)


def _build_architecture_synthesis_prompt(original_prompt: str, *, plan: dict, summary: dict, output_intent: str, max_summary_chars: int) -> str:
    diagram_instruction = {
        "mermaid_sequence_diagram": "Erzeuge ein Mermaid sequenceDiagram fuer die wichtigsten Runtime-Flows.",
        "mermaid_component_diagram": "Erzeuge ein Mermaid flowchart fuer Komponenten und Abhaengigkeiten.",
        "dependency_map": "Erzeuge ein Mermaid flowchart als Dependency Map.",
    }.get(output_intent, "Erzeuge eine strukturierte Architekturuebersicht; wenn passend, mit Mermaid flowchart.")
    return (
        f"{original_prompt.rstrip()}\n\n---\n\n"
        "Alle geplanten Quellen wurden verarbeitet oder als ausgelassen dokumentiert.\n"
        f"{diagram_instruction}\n"
        "Die finale Antwort muss Diagramm/Markdown, kurze Erklärung, Quellenliste und Coverage-Hinweis enthalten.\n"
        "Jede sichere Komponente und Kante muss durch source_evidence oder processed_refs gedeckt sein; inferred Elemente separat markieren.\n\n"
        f"Plan:\n```json\n{json.dumps({'plan_id': plan.get('plan_id'), 'coverage': plan.get('coverage'), 'output_intent': output_intent}, indent=2, ensure_ascii=False)}\n```\n\n"
        f"Strukturierte Summary:\n```json\n{_summary_for_prompt(summary, max_summary_chars)}\n```"
    )


def _run_architecture_full_scan(
    prompt: str,
    workdir: str,
    *,
    options: list,
    timeout: int,
    model: str | None,
    research_context: dict,
) -> tuple[int, str, str]:
    from agent.cli_backends.sgpt import run_sgpt_command


    profile = dict(research_context.get("retrieval_profile") or {})
    full_scan_enabled = bool(getattr(settings, "ananta_worker_full_scan_enabled", True))
    if not full_scan_enabled:
        return run_sgpt_command(prompt=prompt, options=options, timeout=timeout, model=model, workdir=workdir)

    ctx_cfg = _get_worker_context_cfg()

    def _setting(name: str, default: int, lo: int, hi: int) -> int:
        raw = ctx_cfg.get(name)
        if raw is None:
            raw = getattr(settings, name, default)
        try:
            return max(lo, min(hi, int(raw)))
        except (TypeError, ValueError):
            return default

    budgets = dict(profile.get("budgets") or {})
    budgets.setdefault("max_batches", _setting("ananta_worker_full_scan_max_batches", 8, 1, 64))
    budgets.setdefault("files_per_batch", _setting("ananta_worker_full_scan_files_per_batch", 3, 1, 20))
    budgets.setdefault("max_ref_chars", _setting("ananta_worker_full_scan_max_ref_chars", 4000, 500, 40_000))
    budgets.setdefault("max_summary_chars", _setting("ananta_worker_full_scan_summary_chars", 12000, 1000, 80_000))
    budgets.setdefault("max_total_ref_count", _setting("ananta_worker_full_scan_max_total_ref_count", 120, 1, 500))
    profile["budgets"] = budgets
    research_context = dict(research_context)
    research_context["retrieval_profile"] = profile

    planner = _ctx.architecture_analysis_planner
    plan = planner.build_plan(query=prompt, research_context=research_context, retrieval_profile=profile)
    rag_dir = pathlib.Path(workdir) / "rag_helper"
    plan_path = rag_dir / "architecture-plan.json"
    progress_json_path = rag_dir / "architecture-progress.json"
    summary_path = rag_dir / "architecture-summary.json"
    diagrams_path = rag_dir / "architecture-diagrams.md"
    progress_md_path = rag_dir / "progress.md"
    _write_json(plan_path, plan)

    existing_progress = {}
    if progress_json_path.exists():
        try:
            existing_progress = json.loads(progress_json_path.read_text(encoding="utf-8"))
        except Exception:
            existing_progress = {}
    processed_batch_ids = set()
    if existing_progress.get("plan_id") == plan.get("plan_id"):
        processed_batch_ids = {str(item) for item in list(existing_progress.get("processed_batch_ids") or [])}

    if summary_path.exists() and existing_progress.get("plan_id") == plan.get("plan_id"):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = _summary_empty(plan)
    else:
        summary = _summary_empty(plan)

    files_per_batch = int((plan.get("budget") or {}).get("files_per_batch") or 3)
    max_batches = int((plan.get("budget") or {}).get("max_batches") or 8)
    max_ref_chars = int((plan.get("budget") or {}).get("max_ref_chars") or 4000)
    max_summary_chars = int((plan.get("budget") or {}).get("max_summary_chars") or 12000)
    batches = _load_source_file_batches(
        workdir,
        files_per_batch=files_per_batch,
        per_file_chars=max_ref_chars,
        max_files=files_per_batch * max_batches,
        context_lines=_bounded_worker_int("ananta_worker_context_line_window", 5, 0, _MAX_LINE_WINDOW),
        max_snippet_chars=max_ref_chars,
    )

    progress_parts: list[str] = []
    if progress_md_path.exists() and processed_batch_ids:
        try:
            existing_md = progress_md_path.read_text(encoding="utf-8", errors="replace").strip()
            if existing_md:
                progress_parts.append(existing_md)
        except OSError:
            pass

    total = min(len(batches), max_batches)
    last_rc, last_out, last_err = 0, "", ""
    processed_ids = list(processed_batch_ids)

    for step, batch in enumerate(batches[:max_batches], start=1):
        planned_batch = (plan.get("batches") or [{}])[step - 1] if step - 1 < len(plan.get("batches") or []) else {}
        batch_id = str(planned_batch.get("batch_id") or f"batch:{step}")
        if batch_id in processed_batch_ids:
            continue
        iter_prompt = _build_architecture_batch_prompt(
            prompt,
            batch=batch,
            summary=summary,
            step=step,
            total_steps=total,
            max_summary_chars=max_summary_chars,
        )
        rc, out, err = run_sgpt_command(prompt=iter_prompt, options=options, timeout=timeout, model=model, workdir=workdir)
        last_rc, last_err = rc, err
        if out:
            last_out = out
        parsed = _extract_json_object(out)
        summary = _merge_batch_analysis(summary, parsed, batch=batch, raw_output=out)
        source_labels = []
        for block in batch:
            label = str(block.get("rel_path") or "")
            if block.get("start_line") is not None and block.get("end_line") is not None:
                label = f"{label}:{block.get('start_line')}-{block.get('end_line')}"
            source_labels.append(f"{label} [{block.get('source_kind') or 'unknown'}]")
        progress_parts.append(f"## Architektur-Batch {step} — {', '.join(source_labels)}\n\n{str(out or '').strip()}")
        processed_batch_ids.add(batch_id)
        processed_ids.append(batch_id)
        progress_payload = {
            "schema": "architecture_analysis_progress.v1",
            "status": "partial" if rc != 0 else "in_progress",
            "plan_id": plan.get("plan_id"),
            "processed_batch_ids": processed_ids,
            "processed_batches": len(processed_batch_ids),
            "batch_count": total,
            "last_successful_batch": step if rc == 0 else max(0, step - 1),
            "artifact_paths": {
                "plan": str(plan_path),
                "progress": str(progress_json_path),
                "summary": str(summary_path),
                "diagrams": str(diagrams_path),
            },
        }
        _write_json(progress_json_path, progress_payload)
        _write_json(summary_path, summary)
        try:
            progress_md_path.parent.mkdir(parents=True, exist_ok=True)
            progress_md_path.write_text("\n\n---\n\n".join(progress_parts), encoding="utf-8")
        except OSError:
            pass
        if rc != 0 and not out:
            summary["status"] = "partial"
            _write_json(summary_path, summary)
            break

    summary["status"] = "done" if int((summary.get("coverage") or {}).get("processed_refs") or 0) >= int((summary.get("coverage") or {}).get("planned_refs") or 0) else summary.get("status", "partial")
    _write_json(summary_path, summary)

    output_intent = str(plan.get("output_intent") or profile.get("output_intent") or "architecture_overview")
    synthesis_prompt = _build_architecture_synthesis_prompt(
        prompt,
        plan=plan,
        summary=summary,
        output_intent=output_intent,
        max_summary_chars=max_summary_chars,
    )
    rc, out, err = run_sgpt_command(prompt=synthesis_prompt, options=options, timeout=timeout, model=model, workdir=workdir)
    if out:
        last_rc, last_out, last_err = rc, out, err
        try:
            diagrams_path.parent.mkdir(parents=True, exist_ok=True)
            diagrams_path.write_text(out.strip() + "\n", encoding="utf-8")
            progress_md_path.write_text(
                "\n\n---\n\n".join(progress_parts) + f"\n\n---\n\n## Finales Ergebnis\n\n{out.strip()}",
                encoding="utf-8",
            )
        except OSError:
            pass
    progress_payload = {
        "schema": "architecture_analysis_progress.v1",
        "status": "done" if last_rc == 0 else "partial",
        "plan_id": plan.get("plan_id"),
        "processed_batch_ids": processed_ids,
        "processed_batches": len(processed_batch_ids),
        "batch_count": total,
        "processed_refs": int((summary.get("coverage") or {}).get("processed_refs") or 0),
        "omitted_refs": int((summary.get("coverage") or {}).get("omitted_refs") or 0),
        "summary_hash": hashlib.sha1(json.dumps(summary, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16],
        "artifact_paths": {
            "plan": str(plan_path),
            "progress": str(progress_json_path),
            "summary": str(summary_path),
            "diagrams": str(diagrams_path),
        },
    }
    _write_json(progress_json_path, progress_payload)
    return last_rc, last_out, last_err
