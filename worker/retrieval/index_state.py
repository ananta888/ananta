from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class RetrievalIndexState:
    index_version: str
    retrieval_model_version: str
    embedding_model_version: str
    workspace_revision: str
    path_hashes: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index_version": self.index_version,
            "retrieval_model_version": self.retrieval_model_version,
            "embedding_model_version": self.embedding_model_version,
            "workspace_revision": self.workspace_revision,
            "path_hashes": dict(self.path_hashes),
        }


def compute_path_hash(content: str) -> str:
    return sha256(str(content or "").encode("utf-8")).hexdigest()


def derive_workspace_revision(*, path_hashes: dict[str, str], revision_hint: str | None = None) -> str:
    if revision_hint:
        return str(revision_hint).strip()
    canonical = "|".join(f"{path}:{path_hashes[path]}" for path in sorted(path_hashes))
    return sha256(canonical.encode("utf-8")).hexdigest()

