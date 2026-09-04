"""Deterministic CodeCompass-compatible verification target selection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class VerificationTargetSelector:
    def __init__(self, catalog_path: Path) -> None:
        self._catalog_path = catalog_path

    def select(self, *, changed_symbols: Iterable[str], explicit_targets: Iterable[str] = ()) -> tuple[str, ...]:
        payload = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        candidates = {str(item["symbol"]): dict(item) for item in payload.get("candidates", [])}
        selected = {
            symbol
            for raw in changed_symbols
            if (symbol := str(raw).strip()) in candidates and candidates[symbol].get("eligible") is True
        }
        if not selected:
            selected = {
                symbol
                for raw in explicit_targets
                if (symbol := str(raw).strip()) in candidates and candidates[symbol].get("eligible") is True
            }
        if not selected:
            raise ValueError("verification_no_bounded_targets")
        return tuple(sorted(selected))


__all__ = ["VerificationTargetSelector"]
