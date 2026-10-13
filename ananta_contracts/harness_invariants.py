"""Authority boundaries shared by native mutation and genome composition."""

IMMUTABLE_GENOME_PATHS = frozenset(
    {
        "schema",
        "code_hash",
        "config_hash",
        "secret_refs",
        "memory.scopes",
        "gates.gate_refs",
        "gates.promotion_ref",
        "verification.require_evidence",
        "context.evidence_preserving_reduction",
        "reducer.preserve_evidence",
        "reducer.verify_fallback",
    }
)
