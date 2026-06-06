"""
RAG Query Normalizer — RCFG-004/005, RTS-003/004/005

Converts a single user query into a deduplicated list of retrieval variants.
The original query is always retained as the first entry.

Modes (RAG_QUERY_NORMALIZE_MODE):
  off      — returns [original_query] only
  keyword  — adds offline keyword-based DE→EN (and optionally EN→DE) expansions
  llm      — reserved; falls back to keyword if not configured

Directions (RAG_QUERY_TRANSLATION_DIRECTIONS, comma-separated):
  de_to_en           — German query → English code tokens
  en_to_de           — English query → German documentation terms
  mixed_code_query   — detect mixed DE+code queries and preserve code tokens
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# DE → EN keyword / glossary mapping
# ---------------------------------------------------------------------------

_DE_TO_EN_VERB_MAP: dict[str, str] = {
    "funktioniert": "works process function",
    "wie funktioniert": "how works process",
    "wie arbeitet": "how works process",
    "verarbeitet": "process handle",
    "erstellt": "create build generate",
    "erzeugt": "create generate produce",
    "laedt": "load fetch read",
    "lädt": "load fetch read",
    "speichert": "save store persist write",
    "startet": "start run launch",
    "stoppt": "stop terminate cancel",
    "plant": "plan schedule",
    "delegiert": "delegate dispatch",
    "prüft": "check validate verify",
    "prueft": "check validate verify",
    "sendet": "send emit dispatch",
    "empfängt": "receive accept",
    "empfaengt": "receive accept",
    "liest": "read parse load",
    "schreibt": "write save update",
    "sucht": "search find query",
    "findet": "find locate search",
    "gibt": "return yield provide",
    "ruft": "call invoke",
    "initialisiert": "initialize init setup",
    "konfiguriert": "configure setup",
    "registriert": "register",
    "verbindet": "connect bind",
    "trennt": "disconnect detach",
    "verwaltet": "manage handle control",
    "berechnet": "calculate compute",
    "analysiert": "analyze parse",
    "baut": "build construct",
    "öffnet": "open",
    "oeffnet": "open",
    "schliesst": "close",
    "schließt": "close",
    "updated": "update",
    "aktualisiert": "update",
}

_DE_TO_EN_NOUN_MAP: dict[str, str] = {
    "aufgabe": "task",
    "aufgaben": "tasks",
    "datei": "file",
    "dateien": "files",
    "konfiguration": "config configuration",
    "einstellung": "setting config",
    "einstellungen": "settings config",
    "quelle": "source",
    "quellen": "sources",
    "artefakt": "artifact",
    "artefakte": "artifacts",
    "berechtigung": "permission",
    "berechtigungen": "permissions",
    "benutzer": "user",
    "nutzer": "user",
    "dienst": "service",
    "dienste": "services",
    "modul": "module",
    "klasse": "class",
    "methode": "method function",
    "funktion": "function",
    "schnittstelle": "interface",
    "endpunkt": "endpoint",
    "endpunkte": "endpoints",
    "anfrage": "request query",
    "antwort": "response",
    "fehler": "error exception bug",
    "protokoll": "log protocol",
    "protokolle": "logs protocol",
    "pfad": "path",
    "verzeichnis": "directory folder",
    "ordner": "directory folder",
    "repo": "repository repo",
    "repository": "repository",
    "index": "index",
    "speicher": "storage store cache",
    "cache": "cache",
    "worker": "worker",
    "agent": "agent",
    "pipeline": "pipeline",
    "test": "test",
    "tests": "tests",
    "strategie": "strategy",
    "richtlinie": "policy",
    "regel": "rule",
    "regeln": "rules",
    "schema": "schema",
    "modell": "model",
    "modelle": "models",
    "profil": "profile",
    "provider": "provider",
    "anbieter": "provider",
    "routing": "routing",
    "embedding": "embedding",
    "vektor": "vector",
    "chunk": "chunk",
    "kontext": "context",
    "zusammenfassung": "summary",
    "beschreibung": "description",
    "hauptverzeichnis": "root directory",
    "ticket": "ticket issue",
    "sprint": "sprint",
    "todo": "todo task",
    "todos": "todos tasks",
}

# EN → DE glossary for finding German docs/comments/artifacts
_EN_TO_DE_MAP: dict[str, str] = {
    "task": "aufgabe",
    "tasks": "aufgaben",
    "file": "datei",
    "files": "dateien",
    "config": "konfiguration einstellung",
    "configuration": "konfiguration",
    "permission": "berechtigung",
    "permissions": "berechtigungen",
    "user": "benutzer nutzer",
    "service": "dienst",
    "module": "modul",
    "function": "funktion methode",
    "method": "methode funktion",
    "error": "fehler",
    "log": "protokoll",
    "logs": "protokolle",
    "path": "pfad",
    "directory": "verzeichnis ordner",
    "folder": "ordner verzeichnis",
    "policy": "richtlinie regel",
    "rule": "regel",
    "model": "modell",
    "profile": "profil",
    "routing": "routing",
    "update": "aktualisiert",
    "create": "erstellt",
    "delete": "löschen",
    "request": "anfrage",
    "response": "antwort",
    "worker": "worker",
    "agent": "agent",
    "index": "index",
    "strategy": "strategie",
    "summary": "zusammenfassung",
    "description": "beschreibung",
}

# Tokens that are code identifiers — never translate these
_CODE_TOKEN_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"  # dotted.path
    r"|[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"              # snake_case
    r"|[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+"                        # CamelCase
    r"|[A-Z]{2,}(?:_[A-Z0-9]+)+"                              # ENV_VAR
    r"|\.[A-Za-z]{1,6}"                                        # .py .json .md
    r")"
)


def _extract_code_tokens(text: str) -> set[str]:
    return set(_CODE_TOKEN_PATTERN.findall(text))


def _is_mixed_code_query(text: str) -> bool:
    """True if query contains both natural language and code tokens."""
    code_tokens = _extract_code_tokens(text)
    if not code_tokens:
        return False
    # Check there are also plain words (not just code tokens)
    stripped = _CODE_TOKEN_PATTERN.sub(" ", text)
    plain_words = [w for w in stripped.split() if len(w) > 2]
    return bool(plain_words)


def _keyword_de_to_en(query: str) -> str | None:
    """Produce an English keyword variant of a German query. Returns None if no mapping hit."""
    q_lower = query.lower()
    code_tokens = _extract_code_tokens(query)

    parts: list[str] = []

    # Always carry code tokens unchanged
    for token in code_tokens:
        parts.append(token)

    hit = False
    for de_phrase, en_tokens in _DE_TO_EN_VERB_MAP.items():
        if de_phrase in q_lower:
            parts.extend(en_tokens.split())
            hit = True

    for de_word, en_tokens in _DE_TO_EN_NOUN_MAP.items():
        if re.search(r'\b' + re.escape(de_word) + r'\b', q_lower):
            parts.extend(en_tokens.split())
            hit = True

    if not hit and not code_tokens:
        return None

    seen: set[str] = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    return " ".join(unique) if unique else None


def _keyword_en_to_de(query: str) -> str | None:
    """Produce German term expansions for English documentation search."""
    q_lower = query.lower()
    parts: list[str] = []

    # Keep code tokens unchanged
    for token in _extract_code_tokens(query):
        parts.append(token)

    hit = False
    for en_word, de_tokens in _EN_TO_DE_MAP.items():
        if re.search(r'\b' + re.escape(en_word) + r'\b', q_lower):
            parts.extend(de_tokens.split())
            hit = True

    if not hit:
        return None

    seen: set[str] = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    return " ".join(unique) if unique else None


def _mixed_code_query_expansion(query: str) -> str | None:
    """
    For mixed DE+code queries: produce a variant that keeps code tokens
    and adds English translations of the German parts.
    """
    if not _is_mixed_code_query(query):
        return None

    code_tokens = _extract_code_tokens(query)
    q_lower = query.lower()
    parts: list[str] = list(code_tokens)

    for de_phrase, en_tokens in _DE_TO_EN_VERB_MAP.items():
        if de_phrase in q_lower:
            parts.extend(en_tokens.split())

    for de_word, en_tokens in _DE_TO_EN_NOUN_MAP.items():
        if re.search(r'\b' + re.escape(de_word) + r'\b', q_lower):
            parts.extend(en_tokens.split())

    if len(parts) <= len(code_tokens):
        return None

    seen: set[str] = set()
    unique = [p for p in parts if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    return " ".join(unique) if unique else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_query(
    query: str,
    *,
    mode: str = "keyword",
    directions: str = "de_to_en",
    _llm_provider: object = None,
) -> list[str]:
    """
    Return a deduplicated list of retrieval variants for *query*.

    The original query is always the first entry.
    Additional entries are added based on *mode* and *directions*.

    Args:
        query: The original user query.
        mode: "off", "keyword", or "llm" (llm falls back to keyword if not configured).
        directions: Comma-separated subset of "de_to_en", "en_to_de", "mixed_code_query".
        _llm_provider: Optional callable for llm-mode (reserved, not yet implemented).
    """
    original = str(query or "").strip()
    if not original:
        return [original]

    if mode == "off":
        return [original]

    active_directions = {d.strip() for d in directions.split(",") if d.strip()}

    if mode == "llm":
        # LLM mode is reserved — fall back to keyword with a note in logs
        # When a real LLM provider is wired up, this path will call it.
        # For now: identical to keyword.
        mode = "keyword"

    variants: list[str] = [original]

    if mode == "keyword":
        if "de_to_en" in active_directions:
            v = _keyword_de_to_en(original)
            if v and v.lower() != original.lower():
                variants.append(v)

        if "mixed_code_query" in active_directions:
            v = _mixed_code_query_expansion(original)
            if v and v not in variants:
                variants.append(v)

        if "en_to_de" in active_directions:
            v = _keyword_en_to_de(original)
            if v and v not in variants:
                variants.append(v)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for v in variants:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            result.append(v)
    return result


def normalize_query_from_settings(query: str) -> list[str]:
    """Convenience wrapper that reads mode and directions from agent settings."""
    try:
        from agent.config import settings
        mode = str(getattr(settings, "rag_query_normalize_mode", "keyword") or "keyword")
        directions = str(getattr(settings, "rag_query_translation_directions", "de_to_en") or "de_to_en")
    except Exception:
        mode = "keyword"
        directions = "de_to_en"
    return normalize_query(query, mode=mode, directions=directions)
