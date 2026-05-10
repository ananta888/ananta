from __future__ import annotations

from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore


class WikiVectorStore(CodeCompassVectorStore):
    """Wiki-specific alias around the existing vector store."""

