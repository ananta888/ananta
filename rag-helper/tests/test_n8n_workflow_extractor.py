"""Tests for the n8n workflow extractor (track codecompass-n8n-workflow-understanding).

Covers N8N-001 (detection/skips), N8N-002 (records/embeddings), N8N-003
(from/to/type relations + graph passthrough), N8N-004 (dispatch via
document_extractor), N8N-005 (redaction/security findings) and the
relation-priority contract for compact_relation_records.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from rag_helper.application.document_extractor import process_snapshot
from rag_helper.application.file_scanner import FileSnapshot
from rag_helper.application.output_formats import (
    build_context_records,
    build_embedding_records,
    build_graph_edges,
    build_graph_nodes,
)
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.relation_compaction import compact_relation_records
from rag_helper.extractors.base import FileSkipped
from rag_helper.extractors.n8n_workflow_extractor import N8nWorkflowExtractor, is_n8n_workflow

FIXTURES = Path(__file__).parent / "fixtures" / "n8n"

SECRET_MARKERS = (
    "THISISSECRET12345",
    "SUPERSECRETVALUE123",
    "tok-SECRET-QUERY-99999",
    "PINDATA-SECRET-abc",
)


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _parse_fixture(name: str, mode: str = "verbose"):
    extractor = N8nWorkflowExtractor(embedding_text_mode=mode)
    return extractor.parse(f"workflows/{name}", _fixture_text(name))


class _DummyExtractor:
    def __init__(self, **kwargs) -> None:
        pass

    def parse(self, *args, **kwargs):
        return [], [], [], {}


class N8nDetectionTests(unittest.TestCase):
    def test_single_workflow_is_detected(self) -> None:
        payload = json.loads(_fixture_text("simple_manual_http_workflow.json"))
        self.assertTrue(is_n8n_workflow(payload))

    def test_package_json_is_not_detected(self) -> None:
        payload = json.loads(_fixture_text("not_a_workflow_package.json"))
        self.assertFalse(is_n8n_workflow(payload))

    def test_package_json_raises_file_skipped(self) -> None:
        extractor = N8nWorkflowExtractor()
        with self.assertRaises(FileSkipped) as ctx:
            extractor.parse("pkg/package.json", _fixture_text("not_a_workflow_package.json"))
        self.assertEqual(ctx.exception.reason, "not_n8n_workflow")

    def test_invalid_json_raises_file_skipped(self) -> None:
        extractor = N8nWorkflowExtractor()
        with self.assertRaises(FileSkipped) as ctx:
            extractor.parse("broken.json", "{not valid json")
        self.assertEqual(ctx.exception.reason, "invalid_json")

    def test_workflow_list_yields_two_workflow_records(self) -> None:
        wf_a = json.loads(_fixture_text("simple_manual_http_workflow.json"))
        wf_b = json.loads(_fixture_text("webhook_if_merge_wait_workflow.json"))
        extractor = N8nWorkflowExtractor()
        index, _, _, stats = extractor.parse("bundle.json", json.dumps([wf_a, wf_b]))
        workflow_records = [r for r in index if r["kind"] == "n8n_workflow"]
        self.assertEqual(len(workflow_records), 2)
        self.assertEqual(len({r["id"] for r in workflow_records}), 2)
        self.assertEqual(stats["workflow_count"], 2)

    def test_process_snapshot_skips_non_workflow_json_without_crash(self) -> None:
        for name, expected_reason in (
            ("not_a_workflow_package.json", "not_n8n_workflow"),
            (None, "invalid_json"),
        ):
            text = _fixture_text(name) if name else "{broken"
            snapshot = FileSnapshot(
                path=Path("/tmp/x.json"), rel_path="x.json", ext="json",
                text=text, size=len(text), sha1="0" * 40,
            )
            result = process_snapshot(
                snapshot, "sig", False, False, False, ProcessingLimits(),
                _DummyExtractor, _DummyExtractor, _DummyExtractor, _DummyExtractor,
                {}, {},
                n8n_extractor_cls=N8nWorkflowExtractor,
            )
            self.assertTrue(result.manifest_entry.get("skipped"))
            self.assertEqual(result.manifest_entry.get("skip_reason"), expected_reason)
            self.assertEqual(result.index, [])

    def test_process_snapshot_without_n8n_extractor_reports_unsupported(self) -> None:
        text = _fixture_text("simple_manual_http_workflow.json")
        snapshot = FileSnapshot(
            path=Path("/tmp/wf.json"), rel_path="wf.json", ext="json",
            text=text, size=len(text), sha1="0" * 40,
        )
        result = process_snapshot(
            snapshot, "sig", False, False, False, ProcessingLimits(),
            _DummyExtractor, _DummyExtractor, _DummyExtractor, _DummyExtractor,
            {}, {},
        )
        self.assertEqual(result.manifest_entry.get("skip_reason"), "unsupported_extension")


class N8nRecordTests(unittest.TestCase):
    def test_simple_workflow_counts_and_fields(self) -> None:
        index, details, relations, stats = _parse_fixture("simple_manual_http_workflow.json")
        workflow = index[0]
        self.assertEqual(workflow["kind"], "n8n_workflow")
        self.assertEqual(workflow["node_count"], 3)
        self.assertEqual(workflow["connection_count"], 2)
        self.assertEqual(workflow["trigger_count"], 1)
        self.assertEqual(workflow["credential_ref_count"], 0)
        self.assertFalse(workflow["has_pin_data"])
        self.assertEqual(stats["kind"], "n8n")
        self.assertEqual(stats["node_count"], 3)

        nodes = [r for r in index if r["kind"] == "n8n_node"]
        self.assertEqual(len(nodes), 3)
        for node in nodes:
            self.assertEqual(node["parent_id"], workflow["id"])
            for field in ("node_id", "name", "node_type", "type_version", "parameter_keys", "role_labels", "summary", "embedding_text"):
                self.assertIn(field, node)
            self.assertTrue(node["embedding_text"])

        http_node = next(n for n in nodes if n["name"] == "Fetch Orders")
        self.assertIn("api_call", http_node["role_labels"])
        trigger_node = next(n for n in nodes if n["name"] == "Manual Trigger")
        self.assertIn("trigger", trigger_node["role_labels"])

    def test_parameter_keys_contain_no_values(self) -> None:
        index, _, _, _ = _parse_fixture("simple_manual_http_workflow.json")
        http_node = next(r for r in index if r.get("name") == "Fetch Orders")
        self.assertEqual(sorted(http_node["parameter_keys"]), ["method", "options", "url"])

    def test_roles_webhook_branch(self) -> None:
        index, _, _, _ = _parse_fixture("webhook_if_merge_wait_workflow.json")
        webhook = next(r for r in index if r.get("name") == "Order Webhook")
        self.assertIn("trigger", webhook["role_labels"])
        self.assertIn("webhook", webhook["role_labels"])
        if_node = next(r for r in index if r.get("name") == "Is Priority")
        self.assertIn("branch", if_node["role_labels"])

    def test_llm_fixture_roles_credentials_and_tags(self) -> None:
        index, _, relations, stats = _parse_fixture("llm_email_triage_workflow.json")
        workflow = index[0]
        self.assertEqual(workflow["node_count"], 4)
        self.assertEqual(workflow["trigger_count"], 1)
        self.assertEqual(workflow["credential_ref_count"], 1)
        self.assertEqual(workflow["tags"], ["ai", "triage"])

        lm_node = next(r for r in index if r.get("name") == "OpenAI Chat Model")
        self.assertIn("llm", lm_node["role_labels"])
        self.assertEqual(lm_node["credential_refs"], [{"name": "OpenAI account", "type": "openAiApi"}])

        cred_records = [r for r in index if r["kind"] == "n8n_credential_ref"]
        self.assertEqual(len(cred_records), 1)
        self.assertEqual(cred_records[0]["credential_type"], "openAiApi")

        llm_edges = [r for r in relations if r.get("type") == "n8n_uses_llm_provider"]
        self.assertGreaterEqual(len(llm_edges), 1)
        cred_edges = [r for r in relations if r.get("type") == "n8n_uses_credential_ref"]
        self.assertEqual(len(cred_edges), 1)
        self.assertEqual(cred_edges[0]["to"], cred_records[0]["id"])

    def test_embedding_records_have_summary_and_roles(self) -> None:
        index, _, _, _ = _parse_fixture("webhook_if_merge_wait_workflow.json")
        embeddings = build_embedding_records(index)
        by_id = {e["id"]: e for e in embeddings}
        self.assertEqual(len(by_id), len(index))
        for entry in embeddings:
            self.assertTrue(entry["embedding_text"], f"empty embedding_text for {entry['id']}")
            self.assertTrue(entry["summary"], f"empty summary for {entry['id']}")

    def test_compact_mode_is_shorter_than_verbose(self) -> None:
        verbose_index, _, _, _ = _parse_fixture("webhook_if_merge_wait_workflow.json", mode="verbose")
        compact_index, _, _, _ = _parse_fixture("webhook_if_merge_wait_workflow.json", mode="compact")
        verbose_len = len(verbose_index[0]["embedding_text"])
        compact_len = len(compact_index[0]["embedding_text"])
        self.assertLess(compact_len, verbose_len)

    def test_no_record_uses_forbidden_source_field(self) -> None:
        for fixture in (
            "simple_manual_http_workflow.json",
            "webhook_if_merge_wait_workflow.json",
            "llm_email_triage_workflow.json",
            "secrets_and_pindata_workflow.json",
        ):
            index, details, relations, _ = _parse_fixture(fixture)
            for record in [*index, *details]:
                self.assertNotIn("source", record, f"forbidden 'source' field in {record.get('id')}")

    def test_no_record_explosion_for_large_workflow(self) -> None:
        nodes = [
            {
                "id": f"n{i}", "name": f"Step {i}", "type": "n8n-nodes-base.set",
                "typeVersion": 3, "position": [i, 0], "parameters": {},
            }
            for i in range(50)
        ]
        workflow = {"name": "Big Flow", "nodes": nodes, "connections": {}}
        extractor = N8nWorkflowExtractor()
        index, details, relations, _ = extractor.parse("big.json", json.dumps(workflow))
        self.assertLessEqual(len(index), 51)
        self.assertLessEqual(len(details), 50)


class N8nRelationTests(unittest.TestCase):
    def test_branch_true_false_and_resume_edges(self) -> None:
        _, _, relations, _ = _parse_fixture("webhook_if_merge_wait_workflow.json")
        types = [r.get("type") for r in relations]
        self.assertIn("n8n_branch_true", types)
        self.assertIn("n8n_branch_false", types)
        self.assertIn("n8n_resume_flow", types)
        self.assertIn("n8n_connects", types)
        self.assertIn("n8n_receives_webhook", types)

    def test_http_edge_carries_method_and_endpoint(self) -> None:
        index, _, relations, _ = _parse_fixture("simple_manual_http_workflow.json")
        http_edges = [r for r in relations if r.get("type") == "n8n_calls_http_endpoint"]
        self.assertEqual(len(http_edges), 1)
        edge = http_edges[0]
        self.assertEqual(edge["http_method"], "GET")
        self.assertEqual(edge["endpoint_path"], "https://api.example.com/orders?limit=10")
        endpoint_records = [r for r in index if r["kind"] == "n8n_http_endpoint"]
        self.assertEqual(len(endpoint_records), 1)
        self.assertEqual(edge["to"], endpoint_records[0]["id"])

    def test_data_field_edges(self) -> None:
        _, _, relations, _ = _parse_fixture("simple_manual_http_workflow.json")
        reads = [r for r in relations if r.get("type") == "n8n_reads_data_field"]
        writes = [r for r in relations if r.get("type") == "n8n_writes_data_field"]
        self.assertTrue(any(r.get("field") == "$json.data.total" for r in reads))
        self.assertTrue(any(r.get("field") == "order_total" for r in writes))

    def test_ai_tool_flow_edge(self) -> None:
        _, _, relations, _ = _parse_fixture("llm_email_triage_workflow.json")
        ai_edges = [r for r in relations if r.get("type") == "n8n_ai_tool_flow"]
        self.assertEqual(len(ai_edges), 1)

    def test_graph_passthrough_has_no_dangling_edges(self) -> None:
        all_index: list[dict] = []
        all_details: list[dict] = []
        all_relations: list[dict] = []
        for fixture in (
            "simple_manual_http_workflow.json",
            "webhook_if_merge_wait_workflow.json",
            "llm_email_triage_workflow.json",
        ):
            index, details, relations, _ = _parse_fixture(fixture)
            all_index.extend(index)
            all_details.extend(details)
            all_relations.extend(relations)

        nodes = build_graph_nodes(all_index, all_details)
        edges = build_graph_edges(all_index, all_details, all_relations)
        node_ids = {n["id"] for n in nodes}
        self.assertTrue(any(n["kind"] == "n8n_workflow" for n in nodes))
        self.assertTrue(any(n["kind"] == "n8n_node" for n in nodes))
        self.assertTrue(any(e["type"] == "parent_child" for e in edges))
        n8n_edges = [e for e in edges if str(e.get("type", "")).startswith("n8n_")]
        self.assertTrue(n8n_edges)
        for edge in edges:
            self.assertIn(edge["source"], node_ids, f"dangling source in {edge}")
            self.assertIn(edge["target"], node_ids, f"dangling target in {edge}")

    def test_subworkflow_local_resolution_and_no_dangling_for_remote(self) -> None:
        caller = {
            "name": "Flow A",
            "nodes": [
                {"id": "e1", "name": "Call Sub", "type": "n8n-nodes-base.executeWorkflow",
                 "typeVersion": 1, "position": [0, 0],
                 "parameters": {"workflowId": {"cachedResultName": "Flow B"}}},
            ],
            "connections": {},
        }
        local_target = {"name": "Flow B", "nodes": [], "connections": {}}
        extractor = N8nWorkflowExtractor()

        index, details, relations, _ = extractor.parse("pair.json", json.dumps([caller, local_target]))
        sub_edges = [r for r in relations if r.get("type") == "n8n_invokes_subworkflow" and r.get("to")]
        self.assertEqual(len(sub_edges), 1)
        flow_b = next(r for r in index if r["kind"] == "n8n_workflow" and r["name"] == "Flow B")
        self.assertEqual(sub_edges[0]["to"], flow_b["id"])

        index2, details2, relations2, _ = extractor.parse("solo.json", json.dumps(caller))
        edges = build_graph_edges(index2, details2, relations2)
        node_ids = {n["id"] for n in build_graph_nodes(index2, details2)}
        for edge in edges:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)

    def test_relation_priority_keeps_flow_edges_under_pruning(self) -> None:
        _, _, relations, _ = _parse_fixture("webhook_if_merge_wait_workflow.json")
        kept, stats = compact_relation_records(relations, max_relation_records_per_file=5)
        kept_types = [r.get("type") for r in kept]
        self.assertEqual(len(kept), 5)
        self.assertNotIn("n8n_reads_data_field", kept_types)
        self.assertIn("n8n_branch_true", kept_types)
        self.assertIn("n8n_branch_false", kept_types)
        self.assertIn("n8n_connects", kept_types)


class N8nSecurityTests(unittest.TestCase):
    def test_no_secret_appears_in_any_output(self) -> None:
        index, details, relations, stats = _parse_fixture("secrets_and_pindata_workflow.json")
        blobs = [
            json.dumps(index), json.dumps(details), json.dumps(relations), json.dumps(stats),
            json.dumps(build_embedding_records(index)),
            json.dumps(build_context_records(details)),
            json.dumps(build_context_records(details, mode="compact")),
            json.dumps(build_graph_nodes(index, details)),
            json.dumps(build_graph_edges(index, details, relations)),
        ]
        for marker in SECRET_MARKERS:
            for blob in blobs:
                self.assertNotIn(marker, blob, f"secret {marker} leaked")

    def test_secret_candidates_are_counted_and_redacted(self) -> None:
        index, details, _, stats = _parse_fixture("secrets_and_pindata_workflow.json")
        self.assertGreaterEqual(stats["redacted_value_count"], 2)
        self.assertGreaterEqual(stats["security_findings"].get("hardcoded_secret_candidate", 0), 2)
        detail = next(d for d in details if d.get("name") == "Call Secret API")
        self.assertEqual(detail["parameters_summary"]["apiKey"], "<redacted>")

    def test_query_token_is_stripped_from_endpoint(self) -> None:
        index, _, relations, _ = _parse_fixture("secrets_and_pindata_workflow.json")
        endpoint = next(r for r in index if r["kind"] == "n8n_http_endpoint")
        self.assertEqual(endpoint["name"], "https://api.example.com/v1/data?limit=5")

    def test_pin_data_flag_without_content(self) -> None:
        index, _, _, stats = _parse_fixture("secrets_and_pindata_workflow.json")
        workflow = index[0]
        self.assertTrue(workflow["has_pin_data"])
        self.assertIn("pin_data_present", workflow["security_findings"])

    def test_public_webhook_and_no_error_branch_findings(self) -> None:
        index, _, _, stats = _parse_fixture("webhook_if_merge_wait_workflow.json")
        workflow = index[0]
        self.assertIn("public_webhook_without_auth_hint", workflow["security_findings"])
        self.assertIn("no_error_branch", workflow["security_findings"])
        # Findings sind im embedding_text auffindbar:
        self.assertIn("public_webhook_without_auth_hint", workflow["embedding_text"])

    def test_error_handling_workflow_has_no_no_error_branch_finding(self) -> None:
        index, _, _, _ = _parse_fixture("llm_email_triage_workflow.json")
        self.assertNotIn("no_error_branch", index[0]["security_findings"])
        index2, _, _, _ = _parse_fixture("simple_manual_http_workflow.json")
        self.assertNotIn("no_error_branch", index2[0]["security_findings"])

    def test_credential_refs_have_no_ids_or_values(self) -> None:
        index, _, _, _ = _parse_fixture("llm_email_triage_workflow.json")
        blob = json.dumps(index)
        self.assertNotIn('"42"', blob.replace('"node_id"', ""))


if __name__ == "__main__":
    unittest.main()
