"""Embedding generation, embedding cache, and provider abstraction.

This module is reserved for embedding-related functionality extracted from
rag_helper_index_service. Currently no embedding code lives in that service
(the rag-helper tool performs all embedding externally); this module exists
as the designated home for future embedding logic.
"""

from __future__ import annotations
