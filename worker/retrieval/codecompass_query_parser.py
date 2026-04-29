from __future__ import annotations

import re

_JAVA_FQN = re.compile(r"\b(?:[a-z_][a-z0-9_]*\.)+[A-Z][A-Za-z0-9_]*\b")
_JAVA_CLASS = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b")
_METHOD = re.compile(r"\b[a-z_][A-Za-z0-9_]{2,}\s*\(")
_XML_TAG = re.compile(r"</?([A-Za-z_][A-Za-z0-9_.-]*)")
_PROPERTY_KEY = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_.-]*\.[a-zA-Z0-9_.-]+\b")
_ERROR_TOKEN = re.compile(r"\b(?:Exception|Error|Timeout|NullPointerException|IllegalStateException)\b")
_DOT_METHOD = re.compile(r"\.([a-z][A-Za-z0-9_]{2,})\b")
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{2,}")


def parse_codecompass_query(query: str) -> dict[str, list[str]]:
    raw = str(query or "")
    exact_symbols: list[str] = []
    exact_symbols.extend(_JAVA_FQN.findall(raw))
    exact_symbols.extend(_JAVA_CLASS.findall(raw))
    exact_symbols.extend(match.strip().rstrip("(") for match in _METHOD.findall(raw))
    exact_symbols.extend(_DOT_METHOD.findall(raw))
    exact_symbols.extend(_PROPERTY_KEY.findall(raw))
    exact_symbols.extend(_ERROR_TOKEN.findall(raw))
    exact_symbols.extend([tag for tag in _XML_TAG.findall(raw) if tag])

    normalized_exact = sorted({item.strip() for item in exact_symbols if str(item).strip()})
    phrases = sorted({phrase.strip() for phrase in re.findall(r'"([^"]+)"', raw) if phrase.strip()})
    broad_tokens = sorted({token.lower() for token in _TOKEN.findall(raw) if token.strip()})
    return {
        "exact_symbol_terms": normalized_exact,
        "phrase_terms": phrases,
        "broad_terms": broad_tokens,
    }
