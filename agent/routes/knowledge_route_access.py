"""Source-control actions and authorization scopes per knowledge endpoint."""

from __future__ import annotations

from agent.services.source_control_access_policy import SourceControlAction

KNOWLEDGE_ACTIONS = {
    "list_knowledge_collections": SourceControlAction.list,
    "create_knowledge_collection": SourceControlAction.index,
    "get_knowledge_collection": SourceControlAction.detail,
    "index_knowledge_collection": SourceControlAction.index,
    "list_knowledge_index_profiles": SourceControlAction.list,
    "get_knowledge_index_job": SourceControlAction.detail,
    "reconcile_expired_knowledge_index_dispatch": SourceControlAction.index,
    "reconcile_knowledge_index_completion": SourceControlAction.index,
    "list_wiki_import_jobs": SourceControlAction.list,
    "get_wiki_import_job": SourceControlAction.detail,
    "pause_wiki_import_job": SourceControlAction.index,
    "resume_wiki_import_job": SourceControlAction.index,
    "cancel_wiki_import_job": SourceControlAction.delete,
    "retry_interrupted_wiki_import_job": SourceControlAction.index,
    "wiki_disk_state": SourceControlAction.artifact,
    "list_wiki_import_presets": SourceControlAction.list,
    "index_knowledge_source_records": SourceControlAction.index,
    "import_wiki_corpus": SourceControlAction.index,
    "import_wiki_corpus_from_url": SourceControlAction.download,
    "search_knowledge_collection": SourceControlAction.query,
    "search_wiki": SourceControlAction.query,
    "get_knowledge_retrieval_preflight": SourceControlAction.list,
    "list_knowledge_indices": SourceControlAction.list,
    "update_knowledge_index_security_metadata": SourceControlAction.policy,
    "batch_update_knowledge_index_security_metadata": SourceControlAction.policy,
    "get_knowledge_orchestration_contract": SourceControlAction.list,
    "get_file_type_support": SourceControlAction.list,
}
KNOWLEDGE_COLLECTION_ENDPOINTS = {
    "list_knowledge_collections",
    "create_knowledge_collection",
    "list_knowledge_index_profiles",
    "list_wiki_import_jobs",
    "list_wiki_import_presets",
    "get_knowledge_retrieval_preflight",
    "list_knowledge_indices",
    "batch_update_knowledge_index_security_metadata",
    "get_knowledge_orchestration_contract",
    "get_file_type_support",
}
KNOWLEDGE_GLOBAL_ENDPOINTS = {
    "wiki_disk_state",
    "import_wiki_corpus",
    "import_wiki_corpus_from_url",
    "search_wiki",
}
