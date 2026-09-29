"""Hybrid-RAG retrieval settings composed into the application settings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class HybridRagSettings(BaseSettings):
    """Configuration boundary for hybrid RAG retrieval budgets, routing quotas and sources."""

    rag_enabled: bool = Field(default=True, validation_alias="RAG_ENABLED")
    rag_repo_root: str = Field(default=".", validation_alias="RAG_REPO_ROOT")
    rag_data_roots: str = Field(default="docs,data", validation_alias="RAG_DATA_ROOTS")
    rag_max_context_chars: int = Field(default=12000, validation_alias="RAG_MAX_CONTEXT_CHARS")
    rag_max_context_tokens: int = Field(default=3000, validation_alias="RAG_MAX_CONTEXT_TOKENS")
    rag_max_chunks: int = Field(default=40, validation_alias="RAG_MAX_CHUNKS")
    rag_agentic_max_commands: int = Field(default=3, validation_alias="RAG_AGENTIC_MAX_COMMANDS")
    rag_agentic_timeout_seconds: int = Field(default=8, validation_alias="RAG_AGENTIC_TIMEOUT_SECONDS")
    rag_semantic_persist_dir: str = Field(default=".rag/llamaindex", validation_alias="RAG_SEMANTIC_PERSIST_DIR")
    rag_redact_sensitive: bool = Field(default=True, validation_alias="RAG_REDACT_SENSITIVE")
    rag_route_quota_code_repo: int = Field(default=12, validation_alias="RAG_ROUTE_QUOTA_CODE_REPO")
    rag_route_quota_code_semantic: int = Field(default=2, validation_alias="RAG_ROUTE_QUOTA_CODE_SEMANTIC")
    rag_route_quota_docs_semantic: int = Field(default=4, validation_alias="RAG_ROUTE_QUOTA_DOCS_SEMANTIC")
    rag_route_quota_docs_repo: int = Field(default=2, validation_alias="RAG_ROUTE_QUOTA_DOCS_REPO")
    rag_route_quota_fs_agentic: int = Field(default=3, validation_alias="RAG_ROUTE_QUOTA_FS_AGENTIC")
    rag_route_quota_fs_repo: int = Field(default=2, validation_alias="RAG_ROUTE_QUOTA_FS_REPO")
    rag_route_quota_default_repo: int = Field(default=6, validation_alias="RAG_ROUTE_QUOTA_DEFAULT_REPO")
    rag_route_quota_default_semantic: int = Field(default=4, validation_alias="RAG_ROUTE_QUOTA_DEFAULT_SEMANTIC")
    rag_route_quota_codecompass_vector: int = Field(default=6, validation_alias="RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR")
    rag_route_quota_codecompass_vector_default: int = Field(
        default=4, validation_alias="RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR_DEFAULT"
    )
    rag_route_quota_codecompass_vector_docs: int = Field(
        default=1, validation_alias="RAG_ROUTE_QUOTA_CODECOMPASS_VECTOR_DOCS"
    )
    obsidian_vaults: dict = Field(default_factory=dict, validation_alias="ANANTA_OBSIDIAN_VAULTS")
    obsidian_ranking_factor: float = Field(default=0.7, validation_alias="ANANTA_OBSIDIAN_RANKING_FACTOR")
    rag_scan_exclude_dirs: str = Field(
        default=".git,.venv,venv,myvenv,site-packages,node_modules,__pycache__,.mypy_cache,.claude,project-workspaces,.tox,dist,build,.eggs",
        validation_alias="RAG_SCAN_EXCLUDE_DIRS",
    )
    rag_query_normalize_lang: str = Field(default="de,en", validation_alias="RAG_QUERY_NORMALIZE_LANG")
    rag_query_normalize_mode: str = Field(default="keyword", validation_alias="RAG_QUERY_NORMALIZE_MODE")
    rag_query_translation_directions: str = Field(
        default="de_to_en",
        validation_alias="RAG_QUERY_TRANSLATION_DIRECTIONS",
    )
    rag_path_focus_aliases: dict = Field(default_factory=dict, validation_alias="RAG_PATH_FOCUS_ALIASES")
    rag_path_focus_alias_anchor_boost: float = Field(default=0.85, validation_alias="RAG_PATH_FOCUS_ALIAS_ANCHOR_BOOST")
    rag_source_repo_enabled: bool = Field(default=True, validation_alias="RAG_SOURCE_REPO_ENABLED")
    rag_source_artifact_enabled: bool = Field(default=True, validation_alias="RAG_SOURCE_ARTIFACT_ENABLED")
    rag_source_task_memory_enabled: bool = Field(default=True, validation_alias="RAG_SOURCE_TASK_MEMORY_ENABLED")
    rag_source_wiki_enabled: bool = Field(default=True, validation_alias="RAG_SOURCE_WIKI_ENABLED")
    rag_source_open_notebook_enabled: bool = Field(default=False, validation_alias="RAG_SOURCE_OPEN_NOTEBOOK_ENABLED")
    rag_default_window_profile: str = Field(default="standard_32k", validation_alias="RAG_DEFAULT_WINDOW_PROFILE")
    rag_compact_budget_tokens: int = Field(default=12000, validation_alias="RAG_COMPACT_BUDGET_TOKENS")
    rag_standard_budget_tokens: int = Field(default=32000, validation_alias="RAG_STANDARD_BUDGET_TOKENS")
    rag_full_budget_tokens: int = Field(default=64000, validation_alias="RAG_FULL_BUDGET_TOKENS")
    rag_iterative_import_depth: int = Field(default=0, validation_alias="RAG_ITERATIVE_IMPORT_DEPTH")
    rag_iterative_tool_calls_enabled: bool = Field(default=True, validation_alias="RAG_ITERATIVE_TOOL_CALLS_ENABLED")
    rag_iterative_max_tool_calls: int = Field(default=0, validation_alias="RAG_ITERATIVE_MAX_TOOL_CALLS")
    rag_iterative_max_search_calls: int = Field(default=0, validation_alias="RAG_ITERATIVE_MAX_SEARCH_CALLS")
    rag_iterative_symbol_expand_max: int = Field(default=0, validation_alias="RAG_ITERATIVE_SYMBOL_EXPAND_MAX")
    rag_iterative_catalog_chars: int = Field(default=20000, validation_alias="RAG_ITERATIVE_CATALOG_CHARS")
    rag_iterative_tool_chars_per_file: int = Field(default=20000, validation_alias="RAG_ITERATIVE_TOOL_CHARS_PER_FILE")
    rag_iterative_summarize_reads: bool = Field(default=True, validation_alias="RAG_ITERATIVE_SUMMARIZE_READS")
    rag_iterative_summary_chars: int = Field(default=600, validation_alias="RAG_ITERATIVE_SUMMARY_CHARS")
    rag_iterative_initial_min_files: int = Field(default=3, validation_alias="RAG_ITERATIVE_INITIAL_MIN_FILES")
    rag_iterative_initial_max_files: int = Field(default=8, validation_alias="RAG_ITERATIVE_INITIAL_MAX_FILES")

    @field_validator("rag_default_window_profile")
    @classmethod
    def validate_rag_default_window_profile(cls, v: str) -> str:
        allowed = {"compact_12k", "standard_32k", "full_64k", "extended_128k"}
        val = (v or "").strip().lower() or "standard_32k"
        if val not in allowed:
            raise ValueError(f"RAG_DEFAULT_WINDOW_PROFILE muss einer der folgenden Werte sein: {sorted(allowed)}")
        return val
