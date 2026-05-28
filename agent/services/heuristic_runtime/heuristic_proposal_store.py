"""Heuristic Proposal Store — speichert LLM-Kandidaten als JSON-Dateien."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredProposal:
    proposal_id: str
    content_hash: str
    dsl: dict[str, Any]
    provenance: dict[str, Any]
    source_snapshot_hashes: list[str]
    model: str
    created_at: float
    validation_result: dict[str, Any] = field(default_factory=dict)
    status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "content_hash": self.content_hash,
            "dsl": self.dsl,
            "provenance": self.provenance,
            "source_snapshot_hashes": self.source_snapshot_hashes,
            "model": self.model,
            "created_at": self.created_at,
            "validation_result": self.validation_result,
            "status": self.status,
        }


class HeuristicProposalStore:
    def __init__(self, candidates_dir: str | None = None) -> None:
        if candidates_dir is None:
            candidates_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "..", "heuristics", "candidates"
            )
        self._dir = os.path.abspath(candidates_dir)
        os.makedirs(self._dir, exist_ok=True)
        self._content_hashes: set[str] = set()
        self._load_existing_hashes()

    def store(self, dsl: dict[str, Any], *, model: str = "unknown", source_snapshot_hashes: list[str] | None = None) -> StoredProposal | None:
        """Speichert Kandidaten. Duplikate werden via content_hash erkannt."""
        content_hash = self._compute_content_hash(dsl)
        if content_hash in self._content_hashes:
            return None  # Duplikat

        proposal_id = f"lab_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        provenance = dict(dsl.get("provenance") or {})
        proposal = StoredProposal(
            proposal_id=proposal_id,
            content_hash=content_hash,
            dsl=dsl,
            provenance=provenance,
            source_snapshot_hashes=list(source_snapshot_hashes or []),
            model=model,
            created_at=time.time(),
            status="candidate",  # NIEMALS active
        )

        path = os.path.join(self._dir, f"{proposal_id}.heuristic_proposal.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(proposal.to_dict(), f, indent=2, ensure_ascii=False)

        self._content_hashes.add(content_hash)
        return proposal

    def load(self, proposal_id: str) -> StoredProposal | None:
        path = os.path.join(self._dir, f"{proposal_id}.heuristic_proposal.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return StoredProposal(**{k: data[k] for k in StoredProposal.__dataclass_fields__ if k in data})

    def list_proposals(self) -> list[str]:
        return [
            f[:-len(".heuristic_proposal.json")]
            for f in os.listdir(self._dir)
            if f.endswith(".heuristic_proposal.json")
        ]

    def _compute_content_hash(self, dsl: dict[str, Any]) -> str:
        canonical = json.dumps(dsl, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:32]

    def _load_existing_hashes(self) -> None:
        for fname in os.listdir(self._dir):
            if fname.endswith(".heuristic_proposal.json"):
                try:
                    with open(os.path.join(self._dir, fname), encoding="utf-8") as f:
                        data = json.load(f)
                    if "content_hash" in data:
                        self._content_hashes.add(data["content_hash"])
                except Exception:
                    pass
