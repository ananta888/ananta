"""
HCCA-008 — Quality Guard

Validates that compressed content meets minimum quality thresholds before
the adapter accepts it. Falls back to passthrough if quality is insufficient.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Lines that must survive compression if present in the original
_CRITICAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bERROR\b", re.IGNORECASE),
    re.compile(r"\bEXCEPTION\b", re.IGNORECASE),
    re.compile(r"\bTRACEBACK\b", re.IGNORECASE),
    re.compile(r"\bCRITICAL\b", re.IGNORECASE),
]

# Rough secret indicators for the "no_new_secrets" check
_SECRET_HINT: re.Pattern[str] = re.compile(
    r"sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9\-_\.=]{20,}|AKIA[0-9A-Z]{10,}"
)


@dataclass(frozen=True)
class QualityResult:
    score: float          # 0.0–1.0
    passed: bool
    reason: str
    checks: dict[str, bool]  # individual check results


class QualityGuard:
    """Run deterministic quality checks and aggregate into a pass/fail decision."""

    def __init__(self, min_score: float = 0.7, fallback_on_risk: bool = True) -> None:
        self.min_score = min_score
        self.fallback_on_risk = fallback_on_risk

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, original: str, compressed: str, content_type: str) -> QualityResult:
        """Run all quality checks and return a QualityResult."""
        checks: dict[str, bool] = {}
        penalties: list[float] = []

        # 1. not_empty
        not_empty = bool(compressed.strip())
        checks["not_empty"] = not_empty
        if not not_empty:
            # Hard failure — instant 0
            return QualityResult(
                score=0.0,
                passed=False,
                reason="compressed output is empty",
                checks=checks,
            )

        # 2. length_ratio: compressed should not be *longer* than original
        orig_len = max(len(original), 1)
        comp_len = len(compressed)
        length_ok = comp_len <= orig_len * 0.99  # at least 1% shorter
        checks["length_ratio"] = length_ok
        if not length_ok:
            # Mild penalty — compression achieved nothing meaningful
            penalties.append(0.15)

        # 3. min_reduction: must achieve > 10% character reduction
        reduction = (orig_len - comp_len) / orig_len
        min_reduction_ok = reduction >= 0.10
        checks["min_reduction"] = min_reduction_ok
        if not min_reduction_ok:
            penalties.append(0.10)

        # 4. error_lines_preserved
        error_lines_ok = self._critical_lines_preserved(original, compressed)
        checks["error_lines_preserved"] = error_lines_ok
        if not error_lines_ok:
            penalties.append(0.30)  # big penalty — safety-relevant lines lost

        # 5. json_valid
        json_valid_ok = self._json_validity_check(original, compressed, content_type)
        checks["json_valid"] = json_valid_ok
        if not json_valid_ok:
            penalties.append(0.25)

        # 6. no_new_secrets_introduced
        no_new_secrets = self._no_new_secrets(original, compressed)
        checks["no_new_secrets_introduced"] = no_new_secrets
        if not no_new_secrets:
            penalties.append(0.40)  # largest penalty

        total_penalty = min(sum(penalties), 1.0)
        score = round(1.0 - total_penalty, 3)
        passed = score >= self.min_score

        failed_checks = [k for k, v in checks.items() if not v]
        reason = "ok" if passed else f"failed checks: {', '.join(failed_checks)}"

        return QualityResult(score=score, passed=passed, reason=reason, checks=checks)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _critical_lines_preserved(original: str, compressed: str) -> bool:
        """Ensure every critical signal present in original is also in compressed."""
        for pattern in _CRITICAL_PATTERNS:
            if pattern.search(original) and not pattern.search(compressed):
                log.debug("QualityGuard: critical pattern '%s' lost in compression", pattern.pattern)
                return False
        return True

    @staticmethod
    def _json_validity_check(original: str, compressed: str, content_type: str) -> bool:
        """If original was valid JSON and content_type is 'json', compressed must be too."""
        if content_type != "json":
            return True
        try:
            json.loads(original.strip())
        except (json.JSONDecodeError, ValueError):
            return True  # original wasn't valid JSON — no expectation
        try:
            json.loads(compressed.strip())
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    @staticmethod
    def _no_new_secrets(original: str, compressed: str) -> bool:
        """Compressed must not contain secret patterns that original did not."""
        orig_secrets = set(_SECRET_HINT.findall(original))
        comp_secrets = set(_SECRET_HINT.findall(compressed))
        new_secrets = comp_secrets - orig_secrets
        if new_secrets:
            log.warning("QualityGuard: new secret-like strings appeared in compressed output")
            return False
        return True
