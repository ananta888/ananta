"""Query Intent Extractor — Phase 0 vor CodeCompass-Suche.

Drei Modi (konfigurierbar):
  regex        – Regex-Stripper: Meta-Instruktionen und Negationen herausfiltern
  regex_embed  – Regex + Embedding-Distillation via sentence-transformers
  llm          – Mini-LLM-Reformulation (ein zusätzlicher LLM-Call)

Liefert ein QueryIntent-Objekt:
  search_query     – bereinigte Suchanfrage für CodeCompass
  negations        – extrahierte Ausschluss-Terme
  meta_instruction – erkannte Format-/Output-Anweisungen (für den LLM-Prompt)
  mode_used        – welcher Modus tatsächlich angewendet wurde
  original         – unveränderter Original-Text
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# ── Regex-Muster ──────────────────────────────────────────────────────────────

# Format-/Output-Anweisungen (DE+EN) die nicht nach Code suchen sollen
_META_PATTERNS: list[str] = [
    # explizite Ausgabe-Anweisungen
    r"(?:erstell[e]?|generier[e]?|erzeug[e]?|mach[e]?|zeig[e]?(?:\s+mir)?|schreib[e]?)\s+(?:ein(?:en?|em?)?\s+)?(?:mermaid[\s-]?diagramm?|diagramm?|tabelle|liste|übersicht|zusammenfassung|grafik|chart|graph)\b",
    r"(?:create|generate|make|produce|output|write|draw|render|show)\s+(?:a(?:n?)?\s+)?(?:mermaid[\s-]?diagram?|diagram?|table|list|summary|chart|graph|overview)\b",
    # Output-Format-Klauseln
    r"\b(?:als?|as|in|im|format(?:ier[e]?)?)\s+(?:mermaid|markdown|json|yaml|xml|csv|tabelle|table|diagramm?|diagram?|liste|list|html)\b",
    r"\bformat(?:ier[e]?)?\s+(?:als?|as)\b[^,\.;]*",
    # "und erstelle dann ..."
    r"(?:und\s+)?(?:dann\s+)?(?:erstell[e]?|generier[e]?|erzeug[e]?)\s+(?:daraus\s+)?(?:ein(?:en?|em?)?\s+)?(?:mermaid|diagramm?|diagram?|tabelle|grafik)\b[^,\.;]*",
]

# Negations-Muster — werden separat extrahiert, nicht einfach gelöscht
_NEGATION_PATTERNS: list[tuple[str, int]] = [
    # (Muster, Gruppe mit dem Ausschluss-Begriff)
    (r"(?:aber\s+)?nicht\s+(?:die\s+|das\s+|den\s+)?([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\-\.]+)", 1),
    (r"(?:but\s+)?not\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_\-\.]+)", 1),
    (r"ohne\s+(?:die\s+|das\s+|den\s+)?([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\-\.]+)", 1),
    (r"without\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_\-\.]+)", 1),
    (r"(?:ausser|außer)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\-\.]+)", 1),
    (r"except\s+(?:for\s+)?(?:the\s+)?([A-Za-z][A-Za-z0-9_\-\.]+)", 1),
    (r"ignorier[e]?\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\-\.]+)", 1),
    (r"ignore\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_\-\.]+)", 1),
]

# "betrachte nur" / "look only at" — Fokus-Klauseln behalten
_FOCUS_STRIP: list[str] = [
    r"^(?:betrachte|schaue?(?:\s+dir)?|analysier[e]?|untersuche?|prüfe?|check|look(?:\s+at)?|examine|consider)\s+(?:nur\s+)?(?:only\s+)?",
    r"^(?:mir\s+)?(?:geht\s+es\s+)?(?:darum|um)\s+",
]

_compiled_meta: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in _META_PATTERNS]
_compiled_neg: list[tuple[re.Pattern[str], int]] = [(re.compile(p, re.IGNORECASE), g) for p, g in _NEGATION_PATTERNS]
_compiled_focus: list[re.Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in _FOCUS_STRIP]

# Template-Embeddings für Distillation (einmalig gecached)
_CODE_ANCHOR = "source code function class module file implementation API"
_META_ANCHOR = "create generate diagram chart table format output show render"
_ANCHOR_VECS: dict[str, Any] | None = None


# ── Datenklasse ───────────────────────────────────────────────────────────────

@dataclass
class QueryIntent:
    search_query: str
    original: str
    negations: list[str] = field(default_factory=list)
    meta_instruction: str = ""
    mode_used: str = "off"


# ── Haupt-API ─────────────────────────────────────────────────────────────────

def extract_query_intent(
    text: str,
    cfg: dict[str, Any] | None = None,
) -> QueryIntent:
    """Extrahiere den Such-Intent aus *text* gemäß konfiguriertem Modus."""
    mode = str((cfg or {}).get("query_reform_mode") or "off").strip().lower()
    original = text

    if mode == "off" or not text.strip():
        return QueryIntent(search_query=text, original=original, mode_used="off")

    if mode in ("regex", "regex_embed"):
        intent = _apply_regex(text)
        if mode == "regex_embed":
            intent = _apply_embedding_distillation(intent)
        intent.mode_used = mode
        log.debug("query_intent mode=%s original=%r → search=%r", mode, original[:80], intent.search_query[:80])
        return intent

    if mode == "llm":
        return _apply_llm_reform(text, cfg or {})

    return QueryIntent(search_query=text, original=original, mode_used="off")


# ── A) Regex-Stripper ─────────────────────────────────────────────────────────

def _apply_regex(text: str) -> QueryIntent:
    negations: list[str] = []
    meta_parts: list[str] = []

    # 1. Negationen extrahieren (vor dem Strippen)
    for pattern, group in _compiled_neg:
        for m in pattern.finditer(text):
            term = m.group(group).strip()
            if term and term.lower() not in negations:
                negations.append(term.lower())

    # 2. Meta-Instruktionen entfernen + merken
    cleaned = text
    for pat in _compiled_meta:
        found = pat.findall(cleaned)
        if found:
            meta_parts.extend(str(f) for f in found if f)
        cleaned = pat.sub(" ", cleaned)

    # 3. Negations-Klauseln aus der Such-Query entfernen
    for pattern, _ in _compiled_neg:
        cleaned = pattern.sub(" ", cleaned)

    # 4. Fokus-Präfixe normalisieren
    for pat in _compiled_focus:
        cleaned = pat.sub("", cleaned, count=1)

    # 5. Aufräumen
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;.-")

    if not cleaned:
        cleaned = text  # Fallback: nichts strippen wenn alles weg wäre

    return QueryIntent(
        search_query=cleaned,
        original=text,
        negations=negations,
        meta_instruction=" | ".join(meta_parts),
        mode_used="regex",
    )


# ── B) Embedding-Distillation ─────────────────────────────────────────────────

def _load_anchor_vecs() -> dict[str, Any] | None:
    global _ANCHOR_VECS
    if _ANCHOR_VECS is not None:
        return _ANCHOR_VECS
    try:
        from agent.services.restricted_model_inference_service import get_restricted_model_inference_service
        svc = get_restricted_model_inference_service()
        code_vec = svc.embed([_CODE_ANCHOR])[0]
        meta_vec = svc.embed([_META_ANCHOR])[0]
        _ANCHOR_VECS = {"code": code_vec, "meta": meta_vec}
        return _ANCHOR_VECS
    except Exception as exc:
        log.debug("query_intent: anchor vecs not available: %s", exc)
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _apply_embedding_distillation(intent: QueryIntent) -> QueryIntent:
    """Teile die bereinigte Query in Klauseln und behalte nur code-relevante."""
    anchors = _load_anchor_vecs()
    if not anchors:
        return intent  # kein Embedding verfügbar → Regex-Ergebnis behalten

    # Klauseln trennen an ", " / " und " / " and "
    clauses = re.split(r"[,;]|\s+(?:und|and|then|dann)\s+", intent.search_query)
    clauses = [c.strip() for c in clauses if c.strip() and len(c.strip()) > 3]
    if not clauses:
        return intent

    try:
        from agent.services.restricted_model_inference_service import get_restricted_model_inference_service
        svc = get_restricted_model_inference_service()
        vecs = svc.embed(clauses)
    except Exception as exc:
        log.debug("query_intent: embed clauses failed: %s", exc)
        return intent

    code_vec = anchors["code"]
    meta_vec = anchors["meta"]
    kept: list[str] = []
    for clause, vec in zip(clauses, vecs):
        score_code = _cosine(vec, code_vec)
        score_meta = _cosine(vec, meta_vec)
        if score_code >= score_meta or score_code > 0.15:
            kept.append(clause)

    if kept:
        intent.search_query = " ".join(kept)
    return intent


# ── C) Mini-LLM-Reformulation ─────────────────────────────────────────────────

_REFORM_PROMPT = """\
Du bist ein Query-Extraktor. Extrahiere aus der folgenden Nutzereingabe NUR den reinen Suchbegriff für eine Code-Datenbank.

Regeln:
- Entferne Ausgabe-/Format-Anweisungen (z.B. "erstelle Mermaid-Diagramm", "zeige als Tabelle")
- Entferne Negationen und extrahiere sie separat
- Behalte nur was wirklich im Code gesucht werden soll
- Antworte im Format: QUERY: <suchbegriff> | NEGATION: <kommagetrennte ausschlüsse oder leer>

Nutzereingabe: {user_query}
"""


def _apply_llm_reform(text: str, cfg: dict[str, Any]) -> QueryIntent:
    provider = str(cfg.get("query_reform_llm_backend") or cfg.get("chat_backend") or "ananta-worker")
    model = str(cfg.get("query_reform_llm_model") or cfg.get("chat_backend_model") or "")
    prompt = _REFORM_PROMPT.format(user_query=text)
    try:
        from agent.llm_integration import generate_text
        result = generate_text(
            prompt=prompt,
            provider=provider,
            model=model or None,
            max_output_tokens=120,
            temperature=0.0,
        )
        # generate_text returns (text, usage) tuple
        raw = str(result[0] if isinstance(result, tuple) else result or "").strip()
        search_query = text
        negations: list[str] = []

        query_match = re.search(r"QUERY:\s*(.+?)(?:\||\n|$)", raw, re.IGNORECASE)
        if query_match:
            search_query = query_match.group(1).strip()

        neg_match = re.search(r"NEGATION:\s*(.+?)(?:\n|$)", raw, re.IGNORECASE)
        if neg_match:
            neg_raw = neg_match.group(1).strip()
            if neg_raw and neg_raw.lower() not in ("leer", "none", "-", ""):
                negations = [n.strip().lower() for n in neg_raw.split(",") if n.strip()]

        if not search_query:
            search_query = text

        return QueryIntent(
            search_query=search_query,
            original=text,
            negations=negations,
            meta_instruction="",
            mode_used="llm",
        )
    except Exception as exc:
        log.warning("query_intent llm reform failed: %s — falling back to regex", exc)
        intent = _apply_regex(text)
        intent.mode_used = "llm_fallback_regex"
        return intent
