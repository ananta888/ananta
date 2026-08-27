from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest
from jsonschema import Draft202012Validator

from worker.retrieval.sira.config import SiraConfig, SiraMode
from worker.retrieval.sira.contracts import CorpusBinding
from worker.retrieval.sira.query_expander import QueryExpander, classify_exact_query

ROOT = Path(__file__).resolve().parents[1]


class FakeGenerator:
    model_id = "local-test-model"
    model_digest = "sha256:model"
    local = True

    def __init__(self, response: Mapping[str, Any]):
        self.response = response

    def generate(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.response


def binding() -> CorpusBinding:
    return CorpusBinding(
        tenant_id="tenant-a",
        scope="repo-a",
        repository_revision="rev-1",
        source_manifest_hash="manifest-1",
        index_digest="index-1",
        statistics_digest="stats-1",
        profile_version="corpus-discriminative-lexical.v1",
    )


def test_sira_config_is_closed_and_safe_by_default():
    config = SiraConfig.from_mapping({})
    assert config.mode == SiraMode.OFF
    assert config.temperature == 0.0
    assert config.local_models_only is True
    assert "api_key" not in config.safe_dict()
    with pytest.raises(ValueError, match="sira_config_unknown_fields"):
        SiraConfig.from_mapping({"shell_command": "curl example.invalid"})
    with pytest.raises(ValueError, match="sira_temperature_must_be_zero"):
        SiraConfig(temperature=0.1)


def test_exact_queries_bypass_expansion_without_mutating_original():
    config = SiraConfig(mode=SiraMode.PREFERRED, query_model="local-test-model")
    expander = QueryExpander(
        config=config,
        generator=FakeGenerator({"evidence_sketch": "ignored", "terms": [{"value": "other", "confidence": 1.0}]}),
    )
    query = "src/payments/PaymentService.py"
    expansion = expander.expand(query, binding=binding())
    assert classify_exact_query(query) == "exact_path"
    assert expansion.original_query == query
    assert expansion.proposed_terms == ()
    assert expansion.fallback_reason == "bypass_exact_path"


def test_worker_rejects_unattested_generator_under_local_only_policy():
    generator = FakeGenerator({"evidence_sketch": "", "terms": []})
    generator.local = False
    expansion = QueryExpander(
        config=SiraConfig(mode=SiraMode.PREFERRED, query_model="external-model"),
        generator=generator,
    ).expand("payment failure handling", binding=binding())
    assert expansion.proposed_terms == ()
    assert expansion.fallback_reason == "query_model_data_policy_denied"


@pytest.mark.parametrize(
    "schema_name",
    [
        "codecompass.sira-config.v1.json",
        "codecompass.sira-enrichment.v1.json",
        "codecompass.sira-provenance.v1.json",
        "codecompass.sira-retrieval.v1.json",
    ],
)
def test_sira_json_schemas_are_valid(schema_name: str):
    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
