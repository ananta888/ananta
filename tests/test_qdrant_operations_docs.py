from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs/worker/qdrant-vector-store.md"
AVAILABILITY_RUNBOOK = ROOT / "docs/worker/qdrant-vector-store-availability.md"
HUB_READ_RUNBOOK = ROOT / "docs/worker/qdrant-vector-store-hub-read.md"
TASKFLOW_RUNBOOK = ROOT / "docs/worker/qdrant-vector-index-taskflow.md"
WORKER_IDENTITY_RUNBOOK = (
    ROOT / "docs/worker/qdrant-vector-worker-identity.md"
)
TLS_RUNBOOK = ROOT / "docs/worker/qdrant-vector-store-tls.md"
ERROR_CODES = ROOT / "docs/worker/qdrant-vector-store-error-codes.md"
WORKER_OVERLAY = ROOT / "docker/compose-next/compose.qdrant-workers.yml"
WIKI_HUB_READ_OVERLAY = ROOT / "docker/compose-next/compose.qdrant-wiki-hub-read.yml"
_BASH_BLOCK = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)


def test_qdrant_runbook_links_bounded_error_catalog() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    tls_runbook = TLS_RUNBOOK.read_text(encoding="utf-8")
    catalog = ERROR_CODES.read_text(encoding="utf-8")

    assert "(qdrant-vector-store-error-codes.md)" in runbook
    assert "(qdrant-vector-store-tls.md)" in runbook
    assert "(qdrant-vector-store-hub-read.md)" in runbook
    assert "(qdrant-vector-store-availability.md)" in runbook
    assert "(qdrant-vector-worker-identity.md)" in runbook
    assert len(runbook.splitlines()) < 900
    assert len(AVAILABILITY_RUNBOOK.read_text(encoding="utf-8").splitlines()) < 150
    assert len(HUB_READ_RUNBOOK.read_text(encoding="utf-8").splitlines()) < 150
    assert len(tls_runbook.splitlines()) < 250
    assert (
        len(
            WORKER_IDENTITY_RUNBOOK.read_text(
                encoding="utf-8"
            ).splitlines()
        )
        < 150
    )
    assert len(catalog.splitlines()) < 300
    assert (
        runbook.count(
            "compose.workflow-runtime.dev-auth.yml"
        )
        == 2
    )
    assert (
        HUB_READ_RUNBOOK.read_text(encoding="utf-8").count(
            "compose.workflow-runtime.dev-auth.yml"
        )
        == 2
    )
    for heading in (
        "Configuration and rollout",
        "Endpoint and secret policy",
        "Scope, schema and compatibility",
        "Availability and fallback",
        "Task submission, authorization and queue state",
        "Task payload, migration input and worker result",
    ):
        assert f"## {heading}" in catalog


def test_all_qdrant_runbook_shell_blocks_are_parseable() -> None:
    blocks = []
    for path in (
        RUNBOOK,
        HUB_READ_RUNBOOK,
        TASKFLOW_RUNBOOK,
        WORKER_IDENTITY_RUNBOOK,
        TLS_RUNBOOK,
    ):
        blocks.extend(_BASH_BLOCK.findall(path.read_text(encoding="utf-8")))

    assert len(blocks) >= 15
    for index, block in enumerate(blocks, start=1):
        completed = subprocess.run(
            ["bash", "-n"],
            input=block,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, f"bash block {index} is not parseable:\n{completed.stderr}"


def test_full_stack_runbook_commands_use_env_file_and_fail_fast_render() -> None:
    full_stack_blocks = []
    for path in (RUNBOOK, HUB_READ_RUNBOOK):
        full_stack_blocks.extend(
            block
            for block in _BASH_BLOCK.findall(
                path.read_text(encoding="utf-8")
            )
            if "compose.stack.quickstart.yml" in block
        )

    assert len(full_stack_blocks) == 4
    for block in full_stack_blocks:
        assert "test -s .env" in block
        assert "docker compose --env-file .env" in block
        assert "--profile qdrant config --quiet" in block


def test_worker_identity_runbook_documents_registration_readiness_gate() -> None:
    identity = WORKER_IDENTITY_RUNBOOK.read_text(
        encoding="utf-8"
    )

    assert "/internal/worker/vector-index-readiness" in identity
    assert "HTTP 503" in identity
    assert "fresh successful Hub" in identity


def test_snapshot_restore_uses_the_pinned_qdrant_upload_contract() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert ('"$QDRANT_URL/collections/$QDRANT_COLLECTION/snapshots/upload?wait=true&priority=snapshot"') in runbook
    assert '-F "snapshot=@$SNAPSHOT_FILE"' in runbook
    assert 'test "$EXISTING_STATUS" = 404' in runbook
    assert runbook.count('raise SystemExit("restored alias verification failed")') == 1


def test_migration_runbook_uses_the_shared_hub_publisher_root() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    overlay = yaml.safe_load(WORKER_OVERLAY.read_text(encoding="utf-8"))
    hub_root = overlay["services"]["ai-agent-hub"]["environment"]["ANANTA_VECTOR_INDEX_INPUT_PUBLISH_ROOT"]

    assert hub_root == "/var/lib/ananta/vector-index-inputs"
    assert f"export VECTOR_INDEX_INPUT_ROOT={hub_root}" in runbook
    assert "FilesystemVectorIndexInputPublisher" in runbook
    assert '{"path","sha256","scope_fingerprint"}' in runbook
    assert "migration.source_path" in runbook
    assert "/app/data/vector-index-inputs" not in runbook
    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        worker = overlay["services"][worker_name]
        assert worker["environment"]["ANANTA_VECTOR_INDEX_INPUT_ROOTS"] == hub_root
        assert any(mount.endswith(f":{hub_root}:ro") for mount in worker["volumes"])


def test_runbook_starts_wiki_reads_with_the_dedicated_overlay() -> None:
    runbook = HUB_READ_RUNBOOK.read_text(encoding="utf-8")
    overlay = yaml.safe_load(WIKI_HUB_READ_OVERLAY.read_text(encoding="utf-8"))
    environment = overlay["services"]["ai-agent-hub"]["environment"]

    assert "compose.qdrant-wiki-hub-read.yml" in runbook
    assert "ANANTA_WIKI_VECTOR_WORKSPACE_ID" in runbook
    assert "ANANTA_WIKI_VECTOR_SOURCE_ID" in runbook
    assert environment["ANANTA_WIKI_VECTOR_HUB_QDRANT_READ_ENABLED"] == "true"
    assert "CODECOMPASS_VECTOR_ENABLED" not in environment


def test_error_catalog_covers_public_operational_families() -> None:
    catalog = ERROR_CODES.read_text(encoding="utf-8")
    required = {
        "vector_store_invalid_provider",
        "vector_store_invalid_boolean",
        "vector_store_invalid_schema_version",
        "vector_store_backend_schema_conflict",
        "vector_store_endpoint_not_allowlisted",
        "vector_store_secret_not_found",
        "vector_store_json_index_path_override_forbidden",
        "vector_store_qdrant_read_execution_not_configured",
        "codecompass_vector_runtime_scope_incomplete",
        "codecompass_vector_hub_qdrant_read_boolean_invalid",
        "vector_scope_conflict",
        "dimensions_mismatch",
        "migration_required",
        "fallback_state_incompatible",
        "qdrant_unavailable",
        "vector_index_task_conflict",
        "vector_index_reserved_task_ingress_forbidden",
        "vector_index_delegation_required",
        "vector_index_task_attestation_invalid",
        "vector_index_task_domain_binding_invalid",
        "vector_index_goal_purge_cancel_required",
        "vector_index_task_kind_override_forbidden",
        "vector_index_task_assignment_override_forbidden",
        "vector_index_worker_handler_unavailable",
        "vector_store_system_authorization_purpose_invalid",
        "vector_store_system_authorization_actor_invalid",
        "vector_index_task_signing_keyring_required",
        "vector_index_task_verification_keyring_required",
        "vector_index_task_envelope_missing",
        "vector_index_task_outer_id_mismatch",
        "vector_index_task_scope_fingerprint_mismatch",
        "vector_index_replay_retention_invalid",
        "vector_index_replay_clock_invalid",
        "vector_index_task_resolved_config_schema_invalid",
        "vector_index_delete_all_scope_invalid",
        "vector_index_compatibility_incomplete",
        "vector_index_input_ref_digest_mismatch",
        "vector_index_input_ref_sha256_required",
        "vector_index_input_ref_scope_fingerprint_required",
        "vector_index_input_ref_scope_mismatch",
        "vector_index_input_ref_binding_invalid",
        "vector_index_input_ref_path_mismatch",
        "vector_index_input_publisher_digest_mismatch",
        "vector_index_migration_checkpoint_binding_invalid",
        "vector_index_result_idempotency_mismatch",
        "vector_index_result_dispatch_not_admitted",
        "vector_index_result_terminal_conflict",
        "vector_index_result_verification_invalid",
        "vector_index_worker_result_status_invalid",
        "vector_index_dispatch_admission_terminal",
        "vector_index_worker_identity_configuration_invalid",
        "vector_index_worker_role_required",
        "vector_index_worker_composition_not_ready",
        "vector_index_worker_capabilities_not_advertised",
        "vector_index_worker_hub_registration_disabled",
        "vector_index_worker_hub_registration_pending",
        "vector_index_worker_hub_registration_stale",
        "vector_index_worker_hub_registration_identity_mismatch",
        "vector_index_worker_hub_capabilities_incomplete",
        "vector_store_observation_fallback_backends_required",
        "vector_store_observer_required",
        "codecompass_vector_scope_domain_invalid",
        "vector_payload_invalid",
        "vector_payload_too_large",
    }

    missing = sorted(code for code in required if f"`{code}`" not in catalog)
    assert not missing


def test_worker_overlay_separates_private_signer_from_public_verifiers() -> None:
    overlay = yaml.safe_load(WORKER_OVERLAY.read_text(encoding="utf-8"))
    hub = overlay["services"]["ai-agent-hub"]
    assert (
        hub["environment"]["ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE"]
        == "/run/secrets/vector-index-task-signing-keyring.json"
    )
    assert "ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE" not in hub["environment"]
    assert {secret["source"] for secret in hub["secrets"]} == {"vector-index-task-signing-keyring"}

    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        worker = overlay["services"][worker_name]
        assert (
            worker["environment"]["ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE"]
            == "/run/secrets/vector-index-task-verification-keyring.json"
        )
        assert "ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE" not in worker["environment"]
        sources = {secret["source"] for secret in worker["secrets"]}
        assert "vector-index-task-verification-keyring" in sources
        assert "vector-index-task-signing-keyring" not in sources
