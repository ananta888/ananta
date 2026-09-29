"""Query and record scoring for knowledge-index retrieval.

Tokenizes queries, derives task-specific weighting profiles and scores index
records (field hits, path stem, definitions, quality penalties).
"""

from __future__ import annotations

import re
from typing import Any


class KnowledgeIndexRecordScoringMixin:
    """Ranks knowledge-index records against a query.

    Host contract: provides ``_file_type_classifier`` and ``_nested``.
    """

    # Repeats of one query token beyond this count add no further score.
    MAX_COUNTED_REPEATS = 8
    # Score per query token that names the record's file (its stem).
    PATH_STEM_TOKEN_WEIGHT = 2.5
    TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
    # CamelCase boundaries and underscores. Splitting before *every* capital
    # broke ALL_CAPS identifiers into single letters that matched any text.
    SYMBOL_SPLIT_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|_")
    # Stop tokens filtered before scoring. Includes German articles/pronouns and
    # common 2-char fragments produced by splitting words at umlauts (e.g. "erkläre"
    # → ["erkl", "re"]). Without this filter, "re" dominates large Python files
    # through "return", "service", "regex" etc. hitting the linear count formula.
    _STOP_TOKENS: frozenset = frozenset({
        # German articles / pronouns / prepositions
        "der", "die", "das", "den", "dem", "des",
        "ein", "eine", "einer", "eines", "einem",
        "mir", "dir", "ihm", "ihr", "uns",
        "ich", "du", "er", "sie", "wir",
        "und", "oder", "aber", "nicht", "auch", "noch",
        "von", "mit", "bei", "aus", "zur", "zum",
        "ist", "sind", "war", "wird", "hat", "haben",
        "auf", "in", "an", "zu", "am", "im",
        "als", "bitte", "mal", "schon", "wie", "was", "wer", "wo",
        # Common short fragments from umlaut splitting
        "re", "de", "le", "al", "ar", "te", "se",
        # English stop words
        "the", "and", "for", "are", "but", "not", "you", "all",
        "can", "has", "its", "was", "use", "one", "how", "our", "out",
    })
    FILE_KIND_BY_FAMILY = {
        "build": "config",
        "code": "code",
        "configuration": "config",
        "data": "config",
        "diagram": "doc",
        "documentation": "doc",
        "fallback": "other",
        "notebook": "code",
        "script": "code",
        "style": "code",
        "template": "code",
    }
    CODE_KIND_MARKERS = (
        "class",
        "function",
        "method",
        "symbol",
        "type",
        "enum",
        "interface",
        "impl",
        "code",
    )
    DOC_KIND_MARKERS = (
        "md_",
        "doc",
        "readme",
        "adr",
        "guide",
        "architecture",
        "overview",
        "policy",
    )
    RELATION_KIND_MARKERS = ("relation", "edge", "link", "reference", "dependency", "call", "import")
    BROAD_SUMMARY_KIND_MARKERS = ("summary", "overview")

    def _tokenize(self, value: str) -> list[str]:
        return [
            t for t in (token.lower() for token in self.TOKEN_PATTERN.findall(value or ""))
            if len(t) >= 3 and t not in self._STOP_TOKENS
        ]

    def _query_features(self, query: str) -> dict[str, list[str]]:
        tokens = self._tokenize(query)
        symbols: list[str] = []
        for token in tokens:
            has_symbol_shape = (
                "_" in token
                or any(char.isdigit() for char in token)
                or (len(token) >= 6 and any(char.isupper() for char in query))
            )
            if has_symbol_shape:
                symbols.append(token)
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query or ""):
            parts = [part.lower() for part in self.SYMBOL_SPLIT_PATTERN.split(raw) if len(part) >= 3]
            if len(parts) > 1:
                symbols.extend(parts)
        unique_tokens = sorted(set(tokens))
        unique_symbols = sorted(set(symbols))
        return {"tokens": unique_tokens, "symbols": unique_symbols}

    def _task_profile(self, task_kind: str | None, retrieval_intent: str | None) -> dict[str, Any]:
        normalized_kind = str(task_kind or "").strip().lower()
        normalized_intent = str(retrieval_intent or "").strip().lower()
        profile = {
            "record_kind_weights": {"code": 1.0, "doc": 1.0, "relation": 1.0, "other": 1.0},
            "file_kind_weights": {"code": 1.0, "doc": 1.0, "config": 1.0},
            "symbol_multiplier": 1.0,
            "relation_bonus": 0.0,
            "importance_weight": 0.08,
            "duplicate_penalty": 0.12,
            "generated_penalty": 0.18,
            "boilerplate_penalty": 0.08,
            # An explanation asks what a component is; its tests only restate it.
            "test_penalty": 0.2,
        }
        if normalized_kind in {"bugfix", "implement", "coding", "refactor", "test", "testing"}:
            profile["test_penalty"] = 0.0
            profile["record_kind_weights"]["code"] = 1.25
            profile["record_kind_weights"]["relation"] = 1.15
            profile["file_kind_weights"]["code"] = 1.2
            profile["symbol_multiplier"] = 1.15
            profile["relation_bonus"] = 0.35
            profile["importance_weight"] = 0.12
            profile["duplicate_penalty"] = 0.18
        if normalized_kind in {"architecture", "analysis", "doc", "research"}:
            profile["record_kind_weights"]["doc"] = 1.3
            profile["record_kind_weights"]["relation"] = 1.1
            profile["file_kind_weights"]["doc"] = 1.25
            profile["boilerplate_penalty"] = 0.14
        if normalized_kind in {"config", "xml", "ops"}:
            profile["file_kind_weights"]["config"] = 1.35
            profile["record_kind_weights"]["code"] = 1.1
            profile["relation_bonus"] = 0.2
            profile["generated_penalty"] = 0.12

        if "architecture" in normalized_intent or "overview" in normalized_intent:
            profile["record_kind_weights"]["doc"] = max(1.35, profile["record_kind_weights"]["doc"])
            profile["file_kind_weights"]["doc"] = max(1.3, profile["file_kind_weights"]["doc"])
        if normalized_intent == "code_explanation_with_codecompass":
            profile["record_kind_weights"]["doc"] = max(1.45, profile["record_kind_weights"]["doc"])
            profile["file_kind_weights"]["doc"] = max(1.45, profile["file_kind_weights"]["doc"])
            profile["record_kind_weights"]["code"] = min(1.1, profile["record_kind_weights"]["code"])
        if "bug" in normalized_intent or "error" in normalized_intent or "fix" in normalized_intent:
            profile["record_kind_weights"]["code"] = max(1.35, profile["record_kind_weights"]["code"])
            profile["record_kind_weights"]["relation"] = max(1.2, profile["record_kind_weights"]["relation"])
            profile["symbol_multiplier"] = max(1.2, profile["symbol_multiplier"])
            profile["importance_weight"] = max(0.14, float(profile["importance_weight"]))
        if "dependency" in normalized_intent or "neighbor" in normalized_intent or "symbol" in normalized_intent:
            profile["relation_bonus"] = max(0.4, float(profile["relation_bonus"]))

        return profile

    def _duplicate_candidate_ids(self, records: list[tuple[str, dict[str, Any]]]) -> set[str]:
        duplicate_ids: set[str] = set()
        for _filename, record in records:
            relation = str(record.get("relation") or record.get("type") or "").strip().lower()
            if relation != "duplicate_candidate":
                continue
            for key in ("from", "to", "source_id", "target_resolved"):
                value = str(record.get(key) or "").strip()
                if value:
                    duplicate_ids.add(value)
        return duplicate_ids

    def _is_boilerplate_candidate(self, record: dict[str, Any], *, source_hint: str, record_kind: str) -> bool:
        source_lower = str(source_hint or "").lower()
        role_labels = {str(item).strip().lower() for item in list(record.get("role_labels") or []) if str(item).strip()}
        if record.get("generated_code"):
            return True
        if any(marker in source_lower for marker in ("generated/", "generated\\", "target/", "build/generated")):
            return True
        if "dto" in role_labels or "value_object" in role_labels:
            return True
        normalized_kind = str(record_kind or "").strip().lower()
        if any(marker in normalized_kind for marker in self.BROAD_SUMMARY_KIND_MARKERS):
            return True
        return False

    def _record_kind_bucket(self, record_kind: str) -> str:
        normalized = str(record_kind or "").strip().lower()
        if any(marker in normalized for marker in self.CODE_KIND_MARKERS):
            return "code"
        if any(marker in normalized for marker in self.DOC_KIND_MARKERS):
            return "doc"
        if any(marker in normalized for marker in self.RELATION_KIND_MARKERS):
            return "relation"
        return "other"

    def _file_kind_bucket(self, source_hint: str) -> str:
        classification = self._file_type_classifier.classify(
            str(source_hint or ""),
            is_text=True,
        )
        if classification is None:
            return "other"
        return self.FILE_KIND_BY_FAMILY.get(
            classification.descriptor.family,
            "other",
        )

    def _weighted_token_hits(self, tokens: list[str], text: str, weight: float) -> float:
        if not tokens or not text:
            return 0.0
        # Tokens never contain "-": "rag_helper" must also find "rag-helper".
        haystack = text.lower().replace("-", "_")
        score = 0.0
        for token in tokens:
            count = haystack.count(token)
            if count <= 0:
                continue
            # Saturated: a large blob that repeats a token (or merely contains
            # it as a substring, e.g. "rag" in "storage") must not outrank a
            # file whose path and content actually name the query.
            score += weight * (1.0 + min(count - 1, self.MAX_COUNTED_REPEATS) * 0.2)
        return score

    @staticmethod
    def _is_test_path(source_hint: str) -> bool:
        parts = str(source_hint or "").replace("\\", "/").lower().split("/")
        name = parts[-1]
        stem = name.split(".", 1)[0]
        return (
            any(part in {"test", "tests", "__tests__", "spec"} for part in parts[:-1])
            or stem.startswith("test_")
            or stem.endswith("_test")
            or ".spec." in name
            or ".test." in name
        )

    def _score_record(
        self,
        *,
        query: str,
        record: dict[str, Any],
        query_features: dict[str, list[str]],
        field_texts: dict[str, str],
        record_kind: str,
        source_hint: str,
        profile: dict[str, Any],
        duplicate_ids: set[str],
    ) -> tuple[float, dict[str, float]]:
        query_tokens = list(query_features.get("tokens") or [])
        symbol_tokens = list(query_features.get("symbols") or [])
        if not query_tokens:
            return 0.0, {}

        field_weights = {
            "symbol": 4.2,
            "kind": 2.6,
            "path": 2.1,
            "relations": 2.0,
            "summary": 1.5,
            "content": 1.0,
            "focus": 1.9,
        }
        weighted_hits = {
            field: self._weighted_token_hits(query_tokens, field_texts.get(field, ""), weight)
            for field, weight in field_weights.items()
        }
        symbol_hit_score = self._weighted_token_hits(symbol_tokens, field_texts.get("symbol", ""), 2.5) * float(
            profile.get("symbol_multiplier", 1.0)
        )
        phrase_bonus = 0.0
        compact_haystack = re.sub(r"[\s_-]+", " ", " ".join(field_texts.values()).lower())
        normalized_query = re.sub(r"[\s_-]+", " ", str(query or "").strip().lower())
        if normalized_query and normalized_query in compact_haystack:
            phrase_bonus = 1.8

        # A file named after the query ("docs/rag-helper.md",
        # "rag_helper_index_service.py") is about it; a file that merely sits
        # in a matching directory is not, so only the file stem counts here.
        # "rag_helper", "rag-helper" and "rag helper" name the same stem parts.
        stem = str(field_texts.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1].split(".", 1)[0]
        stem_tokens = {token for token in re.split(r"[^a-z0-9]+", stem.lower()) if token}
        query_parts = {part for token in query_tokens for part in token.split("_") if len(part) >= 3}
        path_stem_bonus = self.PATH_STEM_TOKEN_WEIGHT * len(query_parts & stem_tokens)

        definition_bonus = self._definition_bonus(query, record)

        relation_signal = 0.0
        if field_texts.get("relations"):
            relation_signal += 0.7 + float(profile.get("relation_bonus", 0.0))
        if str(record_kind or "").strip().lower().startswith("relation"):
            relation_signal += 0.4

        record_bucket = self._record_kind_bucket(record_kind)
        file_bucket = self._file_kind_bucket(source_hint)
        record_multiplier = float((profile.get("record_kind_weights") or {}).get(record_bucket, 1.0))
        file_multiplier = float((profile.get("file_kind_weights") or {}).get(file_bucket, 1.0))
        base_score = (
            sum(weighted_hits.values()) + symbol_hit_score + phrase_bonus + path_stem_bonus + relation_signal
            + definition_bonus
        )
        importance_score = float(record.get("importance_score") or 0.0)
        importance_boost = min(0.45, importance_score * float(profile.get("importance_weight", 0.0)))
        record_id = str(record.get("id") or "").strip()
        duplicate_penalty = (
            float(profile.get("duplicate_penalty", 0.0))
            if record_id and record_id in duplicate_ids
            else 0.0
        )
        generated_penalty = float(profile.get("generated_penalty", 0.0)) if bool(record.get("generated_code")) else 0.0
        boilerplate_penalty = (
            float(profile.get("boilerplate_penalty", 0.0))
            if self._is_boilerplate_candidate(record, source_hint=source_hint, record_kind=record_kind)
            else 0.0
        )
        test_penalty = float(profile.get("test_penalty", 0.0)) if self._is_test_path(source_hint) else 0.0
        quality_multiplier = max(
            0.35,
            1.0 + importance_boost - duplicate_penalty - generated_penalty - boilerplate_penalty - test_penalty,
        )
        score = base_score * record_multiplier * file_multiplier * quality_multiplier
        return score, {
            "base_score": round(base_score, 4),
            "record_multiplier": round(record_multiplier, 4),
            "file_multiplier": round(file_multiplier, 4),
            "symbol_hit_score": round(symbol_hit_score, 4),
            "relation_signal": round(relation_signal, 4),
            "phrase_bonus": round(phrase_bonus, 4),
            "path_stem_bonus": round(path_stem_bonus, 4),
            "definition_bonus": round(definition_bonus, 4),
            "importance_boost": round(importance_boost, 4),
            "duplicate_penalty": round(duplicate_penalty, 4),
            "generated_penalty": round(generated_penalty, 4),
            "boilerplate_penalty": round(boilerplate_penalty, 4),
            "test_penalty": round(test_penalty, 4),
            "quality_multiplier": round(quality_multiplier, 4),
            "final_score": round(score, 4),
        }

    # A record that defines an identifier from the query ("def X", "class X",
    # "X = ...", "const X") or is named by it is the answer to "where is X";
    # scattered hits of its word parts elsewhere must not outrank it.
    DEFINITION_BONUS = 60.0
    _QUERY_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:_[A-Za-z0-9]+|[a-z0-9][A-Z][A-Za-z0-9]*)")

    def _definition_bonus(self, query: str, record: dict[str, Any]) -> float:
        identifiers = [value for value in self._QUERY_IDENTIFIER.findall(str(query or "")) if len(value) >= 6]
        if not identifiers:
            return 0.0
        symbol = str(record.get("symbol") or self._nested(record, "symbol") or "")
        content = str(record.get("content") or record.get("text") or "")[:200_000]
        for identifier in identifiers[:4]:
            if symbol == identifier or symbol.endswith("." + identifier):
                return self.DEFINITION_BONUS
            pattern = (
                r"(?m)^[ \t]*(?:export[ \t]+)?(?:(?:async[ \t]+)?def|class|const|let|var|function|interface|type)?"
                r"[ \t]*" + re.escape(identifier) + r"\b[ \t]*(?::[^=\n]*)?[=(:]"
            )
            if re.search(pattern, content):
                return self.DEFINITION_BONUS
        return 0.0
