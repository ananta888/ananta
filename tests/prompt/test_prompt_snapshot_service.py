from __future__ import annotations

from agent.services.prompt_snapshot_service import PromptSnapshotService


def test_template_snapshot_hash_stable_for_same_template() -> None:
    service = PromptSnapshotService()
    first = service.build_template_snapshot(
        prompt_template_ref="prompt:worker/default",
        template_path="prompts/worker/default.j2",
        template_version="v1",
        template_text="Summarize {{topic}}",
        renderer="jinja2",
        expected_output_schema_ref="schemas/output/report.v1",
    )
    second = service.build_template_snapshot(
        prompt_template_ref="prompt:worker/default",
        template_path="prompts/worker/default.j2",
        template_version="v1",
        template_text="Summarize {{topic}}",
        renderer="jinja2",
        expected_output_schema_ref="schemas/output/report.v1",
    )
    assert first["template_hash"] == second["template_hash"]


def test_template_snapshot_hash_changes_for_new_content() -> None:
    service = PromptSnapshotService()
    baseline = service.build_template_snapshot(
        prompt_template_ref="prompt:worker/default",
        template_path="prompts/worker/default.j2",
        template_version="v1",
        template_text="Summarize {{topic}}",
        renderer="jinja2",
        expected_output_schema_ref="schemas/output/report.v1",
    )
    changed = service.build_template_snapshot(
        prompt_template_ref="prompt:worker/default",
        template_path="prompts/worker/default.j2",
        template_version="v2",
        template_text="Summarize {{topic}} with {{format}}",
        renderer="jinja2",
        expected_output_schema_ref="schemas/output/report.v1",
    )
    assert baseline["template_hash"] != changed["template_hash"]


def test_final_prompt_record_does_not_store_raw_prompt_by_default() -> None:
    service = PromptSnapshotService()
    record = service.build_final_prompt_record(
        prompt_template_ref="prompt:worker/default",
        variables_payload={"topic": "security"},
        final_prompt_text="Analyze security gaps",
        context_hash="abc123def4567890",
        input_usage_refs=["usage-1"],
        output_schema_ref="schemas/output/report.v1",
        store_raw_prompt=False,
    )
    assert record["raw_prompt_stored"] is False
    assert bool(record["final_prompt_hash"])


def test_final_prompt_snapshot_binds_exact_memory_positions_without_content():
    import hashlib
    import json

    from agent.services.memory_prompt_data import render_memory_data

    block = render_memory_data({
        "source": "source-origin", "content": "untrusted stored text",
        "metadata": {"provenance_ref": "descriptive-origin"},
    })
    prompt = "Trusted policy\n" + block + "\nUser question"
    record = PromptSnapshotService().build_final_prompt_record(
        prompt_template_ref="prompt:worker/default", variables_payload={}, final_prompt_text=prompt,
        context_hash="abc123def4567890", input_usage_refs=[], output_schema_ref="schemas/output/report.v1",
    )
    entry = record["extensions"]["untrusted_memory_blocks"][0]
    assert prompt[entry["start_char"]:entry["end_char"]] == block
    assert entry["block_sha256"] == hashlib.sha256(block.encode()).hexdigest()
    assert entry["source_ref_sha256"] == hashlib.sha256(b"source-origin").hexdigest()
    assert entry["identity_verified"] is False
    assert "untrusted stored text" not in json.dumps(record)
    assert "descriptive-origin" not in json.dumps(record)
