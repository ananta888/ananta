"""CodeCompass index, retrieval and parser-budget settings composed into the application settings."""

from pydantic import Field
from pydantic_settings import BaseSettings


class CodeCompassSettings(BaseSettings):
    """Configuration boundary for CodeCompass FTS/SIRA/vector/graph retrieval and parser limits."""

    codecompass_wiki_index_path: str = Field(
        default="",
        validation_alias="CODECOMPASS_WIKI_INDEX_PATH",
    )
    codecompass_fts_enabled: bool = Field(default=False, validation_alias="CODECOMPASS_FTS_ENABLED")
    codecompass_sira_mode: str = Field(default="off", validation_alias="CODECOMPASS_SIRA_MODE")
    codecompass_sira_online_expansion_enabled: bool = Field(
        default=True,
        validation_alias="CODECOMPASS_SIRA_ONLINE_EXPANSION_ENABLED",
    )
    codecompass_sira_offline_enrichment_enabled: bool = Field(
        default=True,
        validation_alias="CODECOMPASS_SIRA_OFFLINE_ENRICHMENT_ENABLED",
    )
    codecompass_sira_enrichment_model: str = Field(
        default="",
        validation_alias="CODECOMPASS_SIRA_ENRICHMENT_MODEL",
    )
    codecompass_sira_query_model: str = Field(default="", validation_alias="CODECOMPASS_SIRA_QUERY_MODEL")
    codecompass_sira_rerank_model: str = Field(default="", validation_alias="CODECOMPASS_SIRA_RERANK_MODEL")
    codecompass_sira_reranker_enabled: bool = Field(
        default=False,
        validation_alias="CODECOMPASS_SIRA_RERANKER_ENABLED",
    )
    codecompass_sira_local_models_only: bool = Field(
        default=True,
        validation_alias="CODECOMPASS_SIRA_LOCAL_MODELS_ONLY",
    )
    codecompass_sira_snapshot_root: str = Field(
        default="",
        validation_alias="CODECOMPASS_SIRA_SNAPSHOT_ROOT",
    )
    codecompass_sira_layer_root: str = Field(
        default="",
        validation_alias="CODECOMPASS_SIRA_LAYER_ROOT",
    )
    codecompass_sira_rollout_state: str = Field(
        default="data/sira-rollout.sqlite3",
        validation_alias="CODECOMPASS_SIRA_ROLLOUT_STATE",
    )
    codecompass_vector_enabled: bool = Field(default=False, validation_alias="CODECOMPASS_VECTOR_ENABLED")
    codecompass_vector_index_path: str = Field(
        default=".rag/codecompass/vector_index.json",
        validation_alias="CODECOMPASS_VECTOR_INDEX_PATH",
    )
    codecompass_vector_embedding_records_path: str = Field(
        default="rag-helper/out/embedding.json",
        validation_alias="CODECOMPASS_VECTOR_EMBEDDING_RECORDS_PATH",
    )
    codecompass_vector_manifest_path: str = Field(
        default="rag-helper/out/manifest.json",
        validation_alias="CODECOMPASS_VECTOR_MANIFEST_PATH",
    )
    # Canonical file-type rollout and parser resource budgets.  Worker
    # containers read the same explicit environment names; the Hub does not
    # infer worker runtime availability from these values.
    codecompass_file_type_priorities: str = Field(
        default="P0,P1,P2",
        validation_alias="ANANTA_CODECOMPASS_FILE_TYPE_PRIORITIES",
    )
    codecompass_enabled_formats: str = Field(
        default="",
        validation_alias="ANANTA_CODECOMPASS_ENABLED_FORMATS",
    )
    codecompass_disabled_formats: str = Field(
        default="",
        validation_alias="ANANTA_CODECOMPASS_DISABLED_FORMATS",
    )
    codecompass_max_file_bytes: int = Field(
        default=1_048_576,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_FILE_BYTES",
    )
    codecompass_max_lines: int = Field(
        default=50_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_LINES",
    )
    codecompass_parser_timeout_ms: int = Field(
        default=2_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_PARSER_TIMEOUT_MS",
    )
    codecompass_max_output_records: int = Field(
        default=5_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_OUTPUT_RECORDS",
    )
    codecompass_max_xml_nodes: int = Field(
        default=20_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_XML_NODES",
    )
    codecompass_max_xml_depth: int = Field(
        default=64,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_XML_DEPTH",
    )
    codecompass_max_yaml_aliases: int = Field(
        default=50,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_YAML_ALIASES",
    )
    codecompass_max_notebook_cells: int = Field(
        default=2_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_NOTEBOOK_CELLS",
    )
    codecompass_max_notebook_cell_chars: int = Field(
        default=100_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_NOTEBOOK_CELL_CHARS",
    )
    codecompass_max_notebook_output_bytes: int = Field(
        default=0,
        ge=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_NOTEBOOK_OUTPUT_BYTES",
    )
    codecompass_max_csv_rows: int = Field(
        default=10_000,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_CSV_ROWS",
    )
    codecompass_max_csv_columns: int = Field(
        default=256,
        gt=0,
        validation_alias="ANANTA_CODECOMPASS_MAX_CSV_COLUMNS",
    )
    codecompass_vector_embedding_text_profile: str = Field(
        default="codecompass-symbol-path-summary-v1",
        validation_alias="CODECOMPASS_VECTOR_EMBEDDING_TEXT_PROFILE",
    )
    codecompass_vector_fail_mode: str = Field(
        default="degraded_empty",
        validation_alias="CODECOMPASS_VECTOR_FAIL_MODE",
    )
    codecompass_graph_enabled: bool = Field(default=False, validation_alias="CODECOMPASS_GRAPH_ENABLED")
    codecompass_relation_expansion_enabled: bool = Field(
        default=False,
        validation_alias="CODECOMPASS_RELATION_EXPANSION_ENABLED",
    )
    # CCAQE-008: hard bounds for the architecture query engine
    codecompass_query_max_depth: int = Field(default=4, validation_alias="CODECOMPASS_QUERY_MAX_DEPTH")
    codecompass_query_max_nodes: int = Field(default=200, validation_alias="CODECOMPASS_QUERY_MAX_NODES")
    codecompass_query_max_results: int = Field(default=25, validation_alias="CODECOMPASS_QUERY_MAX_RESULTS")
    codecompass_query_max_paths_per_result: int = Field(
        default=3,
        validation_alias="CODECOMPASS_QUERY_MAX_PATHS_PER_RESULT",
    )
    # CCRDS: runtime domain scope (hard path boundaries from domain discovery
    # artifacts / descriptors). Disabled by default — `domain:`-prefixed
    # chat_retrieval_domain_hint values stay soft hints until enabled.
    codecompass_domain_scope_enabled: bool = Field(default=False, validation_alias="CODECOMPASS_DOMAIN_SCOPE_ENABLED")
    codecompass_domain_artifact_path: str = Field(
        default="artifacts/codecompass/domains.detected.json",
        validation_alias="CODECOMPASS_DOMAIN_ARTIFACT_PATH",
    )
    codecompass_domain_descriptor_root: str = Field(
        default="domains", validation_alias="CODECOMPASS_DOMAIN_DESCRIPTOR_ROOT"
    )
    codecompass_scope_strict_mode: bool = Field(default=True, validation_alias="CODECOMPASS_SCOPE_STRICT_MODE")
    codecompass_scope_allow_relation_expansion: bool = Field(
        default=False, validation_alias="CODECOMPASS_SCOPE_ALLOW_RELATION_EXPANSION"
    )
    codecompass_scope_max_external_reference_chunks: int = Field(
        default=2, validation_alias="CODECOMPASS_SCOPE_MAX_EXTERNAL_REFERENCE_CHUNKS"
    )

    # VectorEncoding settings (TQ-018 / VEC-DELTA-004)
    codecompass_vector_encoding_mode: str = Field(
        default="off",
        validation_alias="CODECOMPASS_VECTOR_ENCODING_MODE",
    )
    codecompass_vector_encoding_target_bits: float = Field(
        default=32.0,
        validation_alias="CODECOMPASS_VECTOR_ENCODING_TARGET_BITS",
    )
    codecompass_vector_encoding_seed: int = Field(
        default=888,
        validation_alias="CODECOMPASS_VECTOR_ENCODING_SEED",
    )
    codecompass_vector_encoding_block_size: int = Field(
        default=0,
        validation_alias="CODECOMPASS_VECTOR_ENCODING_BLOCK_SIZE",
    )
    codecompass_vector_encoding_store_original: bool = Field(
        default=False,
        validation_alias="CODECOMPASS_VECTOR_ENCODING_STORE_ORIGINAL",
    )
    # Quantization fallback policy (TQ-014)
    # Values: block | fallback_float32 | warn_only
    codecompass_vector_encoding_fallback_policy: str = Field(
        default="fallback_float32",
        validation_alias="CODECOMPASS_VECTOR_ENCODING_FALLBACK_POLICY",
    )
    # TransformerFeatureProvider settings (TQ-015 / TQ-018)
    # Values: disabled | observe_only | context_first
    codecompass_transformer_feature_mode: str = Field(
        default="disabled",
        validation_alias="CODECOMPASS_TRANSFORMER_FEATURE_MODE",
    )
    codecompass_transformer_feature_model: str = Field(
        default="",
        validation_alias="CODECOMPASS_TRANSFORMER_FEATURE_MODEL",
    )
    codecompass_transformer_feature_local_only: bool = Field(
        default=True,
        validation_alias="CODECOMPASS_TRANSFORMER_FEATURE_LOCAL_ONLY",
    )
    codecompass_transformer_feature_max_input_tokens: int = Field(
        default=512,
        validation_alias="CODECOMPASS_TRANSFORMER_FEATURE_MAX_INPUT_TOKENS",
    )
    # AgentFeatureProvider policy gates (AGENT-FEATURE-004)
    codecompass_agent_feature_enabled: bool = Field(
        default=False,
        validation_alias="CODECOMPASS_AGENT_FEATURE_ENABLED",
    )
    codecompass_agent_feature_external_calls_allowed: bool = Field(
        default=False,
        validation_alias="CODECOMPASS_AGENT_FEATURE_EXTERNAL_CALLS_ALLOWED",
    )
    codecompass_agent_feature_allowed_provider_ids: str = Field(
        default="",
        validation_alias="CODECOMPASS_AGENT_FEATURE_ALLOWED_PROVIDER_IDS",
    )
