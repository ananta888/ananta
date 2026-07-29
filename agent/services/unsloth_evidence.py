"""Fail-closed validation for externally supplied source and run evidence IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)


class EvidenceVerificationError(ValueError):
    """Raised when evidence is missing, malformed, or not externally trusted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EvidenceReferences:
    source_ids: tuple[str, ...]
    run_ids: tuple[str, ...]


class ProvidedEvidenceRegistry:
    """Accepts only IDs supplied by an authoritative caller.

    The registry deliberately has no ID-generation API. This prevents an
    integration adapter from making an ungrounded claim look verified.
    """

    def __init__(
        self,
        *,
        source_ids: Iterable[str] = (),
        run_ids: Iterable[str] = (),
    ) -> None:
        self._source_ids = frozenset(self._normalize(source_ids, kind="source"))
        self._run_ids = frozenset(self._normalize(run_ids, kind="run"))

    def require_source(self, source_id: str | None) -> str:
        return self._require(
            source_id,
            self._source_ids,
            kind="source",
        )

    def require_run(self, run_id: str | None) -> str:
        return self._require(run_id, self._run_ids, kind="run")

    def resolve(
        self,
        *,
        source_ids: Iterable[str],
        run_ids: Iterable[str],
    ) -> EvidenceReferences:
        sources = self._normalize(source_ids, kind="source")
        runs = self._normalize(run_ids, kind="run")
        if not sources:
            raise EvidenceVerificationError(
                "source_id_missing",
                "At least one externally supplied source ID is required.",
            )
        if not runs:
            raise EvidenceVerificationError(
                "run_id_missing",
                "At least one externally supplied run ID is required.",
            )
        for source_id in sources:
            self._require(source_id, self._source_ids, kind="source")
        for run_id in runs:
            self._require(run_id, self._run_ids, kind="run")
        return EvidenceReferences(
            source_ids=sources,
            run_ids=runs,
        )

    @staticmethod
    def _normalize(values: Iterable[str], *, kind: str) -> tuple[str, ...]:
        normalizer = normalize_source_ids if kind == "source" else normalize_run_ids
        try:
            return normalizer(list(values))
        except MlInternTrainingContractError as exc:
            raise EvidenceVerificationError(exc.reason_code, str(exc)) from exc

    @staticmethod
    def _require(
        value: str | None,
        catalog: frozenset[str],
        *,
        kind: str,
    ) -> str:
        if not value:
            raise EvidenceVerificationError(
                f"{kind}_id_missing",
                f"An externally supplied {kind} ID is required.",
            )
        normalized = ProvidedEvidenceRegistry._normalize((value,), kind=kind)[0]
        if normalized not in catalog:
            raise EvidenceVerificationError(
                f"{kind}_id_unknown",
                f"The supplied {kind} ID is not in the trusted catalog.",
            )
        return normalized
