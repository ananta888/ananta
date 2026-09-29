from types import SimpleNamespace

from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
    KnowledgeIndexConsumptionPolicy,
)


def _index(
    *,
    index_id: str = "idx-1",
    source_scope: str = "repository",
    projection_state: str = "projected",
    status: str = "completed",
    binding: object | None = None,
    execution_job_schema: str = KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
):
    execution_binding = (
        {
            "schema": KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
            "projection_state": projection_state,
            "execution_job_schema": execution_job_schema,
            "job_id": "knowledge-index-" + ("1" * 32),
            "knowledge_index_id": index_id,
            "authority_binding_digest": (
                "a" * 64
                if execution_job_schema == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
                else ""
            ),
            "assignment_id": (
                "assignment-1"
                if execution_job_schema == KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
                else ""
            ),
        }
        if binding is None
        else binding
    )
    return SimpleNamespace(
        id=index_id,
        source_scope=source_scope,
        status=status,
        index_metadata={
            KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY: execution_binding
        },
    )


def test_legacy_index_remains_compatible_without_authorized_scope():
    index = SimpleNamespace(
        id="legacy-1",
        source_scope="wiki",
        status="completed",
        index_metadata={},
    )

    decision = KnowledgeIndexConsumptionPolicy().evaluate(index)

    assert decision.allowed is True
    assert decision.bound_v2 is False


def test_bound_index_requires_exact_projected_marker():
    policy = KnowledgeIndexConsumptionPolicy()

    assert not policy.can_consume(
        _index(projection_state="pending"),
        allowed_index_ids={"idx-1"},
    )
    assert not policy.can_consume(
        _index(binding={"schema": "unexpected", "projection_state": "projected"}),
        allowed_index_ids={"idx-1"},
    )
    assert not policy.can_consume(
        _index(
            binding={
                "schema": KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
                "projection_state": "projected",
            }
        ),
        allowed_index_ids={"idx-1"},
    )
    malformed_v2 = _index()
    malformed_v2.index_metadata[
        KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY
    ]["authority_binding_digest"] = "not-a-digest"
    assert not policy.can_consume(
        malformed_v2,
        allowed_index_ids={"idx-1"},
    )
    assert policy.can_consume(
        _index(),
        allowed_index_ids={"idx-1"},
    )


def test_non_artifact_v2_requires_hub_derived_index_allow_list():
    policy = KnowledgeIndexConsumptionPolicy()
    index = _index(index_id="idx-private")

    assert not policy.can_consume(index)
    assert not policy.can_consume(
        index,
        allowed_index_ids={"idx-foreign"},
    )
    assert policy.can_consume(
        index,
        allowed_index_ids={"idx-private"},
    )


def test_projected_artifact_v2_does_not_require_non_artifact_scope():
    assert KnowledgeIndexConsumptionPolicy().can_consume(
        _index(source_scope="artifact")
    )


def test_valid_projected_v1_binding_remains_legacy_compatible():
    decision = KnowledgeIndexConsumptionPolicy().evaluate(
        _index(execution_job_schema=KNOWLEDGE_INDEX_LEGACY_JOB_SCHEMA)
    )

    assert decision.allowed is True
    assert decision.bound_v2 is False
