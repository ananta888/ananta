"""Composition root for Hub-side Knowledge Hygiene services."""

from __future__ import annotations

import threading

from agent.repositories.knowledge_hygiene_repository import SqlKnowledgeHygieneRepository
from agent.services.knowledge_hygiene.config import KnowledgeHygieneConfig
from agent.services.knowledge_hygiene.service import KnowledgeHygieneService
from agent.services.knowledge_hygiene.writeback import ObsidianMarkdownWritebackAdapter


_repository = None
_lock = threading.Lock()


def get_knowledge_hygiene_service(settings_mapping) -> KnowledgeHygieneService:
    global _repository
    if _repository is None:
        with _lock:
            if _repository is None:
                _repository = SqlKnowledgeHygieneRepository()
    config = KnowledgeHygieneConfig.from_mapping(settings_mapping)
    writeback = None
    if config.source_writeback_enabled and config.allowed_obsidian_roots:
        writeback = ObsidianMarkdownWritebackAdapter(
            allowed_roots=config.allowed_obsidian_roots,
            max_patch_bytes=config.max_patch_bytes,
        )
    return KnowledgeHygieneService(_repository, config, writeback=writeback)
