"""TransformerFeatureProvider — TQ-015, TQ-016, TQ-017.

Design contract
---------------
- Uses ONLY ``RestrictedModelInferenceService.rerank()``, ``embed()``,
  ``classify()``, ``score_choices()``.  ``generate()`` is NEVER called.
- Returns structured ``TransformerFeatureResult`` — never free text.
- Policy gates: ``mode``, ``local_only``, ``allowed_model_names``,
  ``max_input_tokens``.

TQ-015  TransformerFeatureProvider.score_candidates()
TQ-016  apply_deterministic_rerank() — merges feature scores, re-sorts
TQ-017  embed_for_context()          — optional embedding for VectorEncoding
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from agent.services.restricted_model_inference_service import (
    RestrictedModelInferenceService,
    get_restricted_model_inference_service,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE_DISABLED = "disabled"
MODE_OBSERVE_ONLY = "observe_only"
MODE_CONTEXT_FIRST = "context_first"

_VALID_MODES = frozenset({MODE_DISABLED, MODE_OBSERVE_ONLY, MODE_CONTEXT_FIRST})

_ENV_MODE = "CODECOMPASS_TRANSFORMER_FEATURE_MODE"
_ENV_MODEL = "CODECOMPASS_TRANSFORMER_FEATURE_MODEL"
_ENV_LOCAL_ONLY = "CODECOMPASS_TRANSFORMER_FEATURE_LOCAL_ONLY"
_ENV_MAX_INPUT_TOKENS = "CODECOMPASS_TRANSFORMER_FEATURE_MAX_INPUT_TOKENS"

# Weights used when context_first re-ranking combines original + feature scores.
_ORIGINAL_WEIGHT = 0.6
_FEATURE_WEIGHT = 0.4


# ---------------------------------------------------------------------------
# Result type (TQ-015)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TransformerFeatureResult:
    """Structured result from transformer feature scoring.  Never free text.

    All fields are set deterministically from adapter outputs.
    """

    feature_scores: list[float]
    feature_labels: list[str]
    confidence: float
    model_version: str
    layer_description: str
    input_hash: str        # sha256 of the serialised input
    elapsed_ms: float
    mode_used: str         # one of: disabled / observe_only / context_first

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_scores": list(self.feature_scores),
            "feature_labels": list(self.feature_labels),
            "confidence": self.confidence,
            "model_version": self.model_version,
            "layer_description": self.layer_description,
            "input_hash": self.input_hash,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "mode_used": self.mode_used,
        }


# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------

@dataclass
class TransformerFeaturePolicy:
    """Runtime policy gates for TransformerFeatureProvider."""

    mode: str = MODE_DISABLED
    local_only: bool = True
    allowed_model_names: list[str] = field(default_factory=list)
    max_input_tokens: int = 512

    def __post_init__(self) -> None:
        mode = str(self.mode or MODE_DISABLED).strip().lower()
        if mode not in _VALID_MODES:
            log.warning(
                "TransformerFeaturePolicy: unknown mode %r, falling back to 'disabled'", mode
            )
            mode = MODE_DISABLED
        self.mode = mode
        self.local_only = bool(self.local_only)
        self.max_input_tokens = max(1, int(self.max_input_tokens))


# ---------------------------------------------------------------------------
# Main provider (TQ-015, TQ-016, TQ-017)
# ---------------------------------------------------------------------------

class TransformerFeatureProvider:
    """Produces structured feature scores from restricted transformer inference.

    Callers MUST use ``apply_deterministic_rerank()`` or their own deterministic
    rule to consume the scores — this provider never makes ordering decisions
    on its own (TQ-016).

    Parameters
    ----------
    policy:
        Runtime policy gates.
    restricted_inference:
        Injected ``RestrictedModelInferenceService``.  Defaults to the global
        singleton if omitted.
    """

    def __init__(
        self,
        policy: TransformerFeaturePolicy | None = None,
        restricted_inference: RestrictedModelInferenceService | None = None,
    ) -> None:
        self._policy = policy or TransformerFeaturePolicy()
        self._inference = restricted_inference or get_restricted_model_inference_service()

    @property
    def policy(self) -> TransformerFeaturePolicy:
        return self._policy

    # ------------------------------------------------------------------
    # TQ-015: score_candidates
    # ------------------------------------------------------------------

    def score_candidates(
        self,
        candidates: list[dict[str, Any]],
        query: str,
    ) -> list[TransformerFeatureResult]:
        """Score a list of candidate dicts against a query string.

        Parameters
        ----------
        candidates:
            Each dict should have at minimum ``"path"`` and/or ``"excerpt"``
            keys, matching the shape expected by
            ``RestrictedModelInferenceService.rerank()``.
        query:
            The search query.

        Returns
        -------
        list[TransformerFeatureResult]
            One result per candidate, in the same order as ``candidates``.
            Returns an empty list in ``disabled`` mode.
        """
        if self._policy.mode == MODE_DISABLED:
            return []

        if not candidates:
            return []

        # Enforce token budget heuristic (1 token ≈ 4 chars, conservative).
        max_chars = self._policy.max_input_tokens * 4
        safe_query = query[:max_chars] if query else ""

        input_repr = f"{safe_query}|{[c.get('path', '') for c in candidates]}"
        input_hash = hashlib.sha256(input_repr.encode("utf-8")).hexdigest()[:24]

        t0 = time.time()
        try:
            rerank_results = self._inference.rerank(safe_query, candidates)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "TransformerFeatureProvider.score_candidates: rerank failed "
                "(mode=%s), returning empty scores. exc=%s",
                self._policy.mode,
                exc,
            )
            return []
        elapsed_ms = (time.time() - t0) * 1000.0

        # Build a lookup by record_id / path for alignment with candidates.
        score_by_id: dict[str, float] = {}
        score_by_path: dict[str, float] = {}
        model_version = ""
        for rr in rerank_results:
            score_by_id[rr.record_id] = rr.score
            score_by_path[rr.path] = rr.score
            if not model_version:
                model_version = rr.model_id or rr.engine

        results: list[TransformerFeatureResult] = []
        for candidate in candidates:
            record_id = str(candidate.get("record_id") or "")
            path = str(candidate.get("path") or "")
            # Prefer record_id match, fall back to path, then 0.0
            feature_score = score_by_id.get(record_id, score_by_path.get(path, 0.0))
            label = path or record_id or "unknown"

            if self._policy.mode == MODE_OBSERVE_ONLY:
                log.debug(
                    "TransformerFeatureProvider observe_only: path=%r score=%.4f",
                    path,
                    feature_score,
                )

            results.append(
                TransformerFeatureResult(
                    feature_scores=[feature_score],
                    feature_labels=[label],
                    confidence=float(feature_score),
                    model_version=model_version,
                    layer_description="rerank_score",
                    input_hash=input_hash,
                    elapsed_ms=elapsed_ms,
                    mode_used=self._policy.mode,
                )
            )

        return results

    # ------------------------------------------------------------------
    # TQ-016: apply_deterministic_rerank
    # ------------------------------------------------------------------

    def apply_deterministic_rerank(
        self,
        rows: list[dict[str, Any]],
        feature_results: list[TransformerFeatureResult],
    ) -> list[dict[str, Any]]:
        """Merge feature scores into rows and re-sort deterministically.

        Ananta controls policy; this method provides the deterministic
        re-ranking step.  The TransformerFeatureProvider only *produces* scores
        (TQ-016).

        In ``observe_only`` mode: scores are written to
        ``_transformer_feature_score`` for observability but sort order is
        unchanged.

        In ``context_first`` mode: each row's combined score is computed as::

            combined = original_score * 0.6 + feature_score * 0.4

        and rows are sorted descending by ``combined``.

        In ``disabled`` mode: rows are returned unchanged.

        Parameters
        ----------
        rows:
            The candidate rows (dicts with at minimum ``"score"`` or
            ``"original_score"`` key).
        feature_results:
            Must be the same length and order as ``rows``.

        Returns
        -------
        list[dict]
            A new list of row dicts (copies) with ``_transformer_feature_score``
            injected.  In ``context_first`` mode the list is also re-sorted.
        """
        mode = self._policy.mode
        if mode == MODE_DISABLED:
            return list(rows)

        if len(rows) != len(feature_results):
            log.warning(
                "TransformerFeatureProvider.apply_deterministic_rerank: "
                "rows(%d) and feature_results(%d) length mismatch — returning rows unchanged",
                len(rows),
                len(feature_results),
            )
            return list(rows)

        enriched: list[dict[str, Any]] = []
        for row, result in zip(rows, feature_results, strict=False):
            row_copy = dict(row)
            feature_score = result.feature_scores[0] if result.feature_scores else 0.0
            row_copy["_transformer_feature_score"] = feature_score
            row_copy["_transformer_model_version"] = result.model_version
            row_copy["_transformer_input_hash"] = result.input_hash

            if mode == MODE_CONTEXT_FIRST:
                original_score = float(
                    row_copy.get("original_score")
                    or row_copy.get("score")
                    or 0.0
                )
                combined = original_score * _ORIGINAL_WEIGHT + feature_score * _FEATURE_WEIGHT
                row_copy["_transformer_combined_score"] = combined

            enriched.append(row_copy)

        if mode == MODE_CONTEXT_FIRST:
            enriched.sort(
                key=lambda r: (
                    -float(r.get("_transformer_combined_score") or 0.0),
                    str(r.get("path") or r.get("record_id") or ""),
                )
            )

        return enriched

    # ------------------------------------------------------------------
    # TQ-017: embed_for_context (experimental)
    # ------------------------------------------------------------------

    def embed_for_context(self, text: str) -> list[float] | None:
        """Produce an embedding via the restricted inference service.

        TQ-017: Optional hidden-state/embedding profile experiment.  The
        embedding can be fed into VectorEncoding downstream.

        Returns ``None`` when mode is ``disabled`` or when the inference call
        fails.  Never raises.

        Parameters
        ----------
        text:
            Input text.  Truncated to ``max_input_tokens * 4`` characters
            before dispatch.

        Returns
        -------
        list[float] | None
            Single embedding vector, or ``None`` on failure/disabled.
        """
        if self._policy.mode == MODE_DISABLED:
            return None

        max_chars = self._policy.max_input_tokens * 4
        safe_text = (text or "")[:max_chars]
        if not safe_text:
            return None

        try:
            vectors = self._inference.embed([safe_text])
            if vectors:
                return list(vectors[0])
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "TransformerFeatureProvider.embed_for_context: embed() failed, "
                "returning None. exc=%s",
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None = None,
        restricted_inference: RestrictedModelInferenceService | None = None,
    ) -> "TransformerFeatureProvider":
        """Build a TransformerFeatureProvider from config dict + env vars.

        Environment variables
        ---------------------
        CODECOMPASS_TRANSFORMER_FEATURE_MODE          default: "disabled"
        CODECOMPASS_TRANSFORMER_FEATURE_MODEL         default: ""
        CODECOMPASS_TRANSFORMER_FEATURE_LOCAL_ONLY    default: "1"
        CODECOMPASS_TRANSFORMER_FEATURE_MAX_INPUT_TOKENS  default: "512"

        Config dict keys take precedence over env vars.
        """
        cfg = dict(config or {})
        env = os.environ

        mode = str(
            cfg.get("mode")
            or env.get(_ENV_MODE)
            or MODE_DISABLED
        ).strip().lower()

        allowed_model_names_raw = str(
            cfg.get("allowed_model_names")
            or env.get(_ENV_MODEL)
            or ""
        )
        allowed_model_names = [
            m.strip() for m in allowed_model_names_raw.split(",") if m.strip()
        ]

        local_only_raw = cfg.get("local_only", env.get(_ENV_LOCAL_ONLY, "1"))
        local_only = _bool(local_only_raw)

        max_input_tokens = int(
            cfg.get("max_input_tokens")
            or env.get(_ENV_MAX_INPUT_TOKENS)
            or 512
        )

        policy = TransformerFeaturePolicy(
            mode=mode,
            local_only=local_only,
            allowed_model_names=allowed_model_names,
            max_input_tokens=max_input_tokens,
        )
        return cls(policy=policy, restricted_inference=restricted_inference)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
