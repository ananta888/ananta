# Qdrant vector-store error codes

This is the operational catalog for the Qdrant vector-store integration. Codes
are bounded identifiers. Diagnostics and metrics must not add workspace IDs,
repository IDs, record IDs, paths, URLs, payload text or secrets as labels.
Some parser errors expose a broad `reason` plus a specific `cause_reason`; both
remain actionable.

## Configuration and rollout

| Code | Operator action |
| --- | --- |
| `vector_store_invalid_provider`, `vector_store_provider_invalid` | Select `json` or `qdrant`; do not infer a fallback. |
| `missing_qdrant_vector_store_config` | Supply a complete `qdrant` mapping before opting in. |
| `vector_store_invalid_availability_policy` | Use a supported fail-fast, degraded-empty or explicit compatible-JSON policy. |
| `vector_store_invalid_collection` | Use a bounded collection prefix and supported strategy. |
| `unsupported_qdrant_collection_strategy`, `unsupported_qdrant_rebuild_strategy`, `unsupported_qdrant_distance` | Restore the documented versioned-collection/alias-swap strategy and supported distance. |
| `vector_store_invalid_distance` | Use `cosine`, `dot` or `euclid` as supported by the active contract. |
| `vector_store_invalid_timeout` | Set connect/request timeouts inside the bounded range and remove conflicting legacy timeout fields. |
| `vector_store_invalid_boolean` | Use an actual JSON/environment boolean accepted by the typed configuration parser. |
| `vector_store_invalid_origin` | Correct and re-review the exact endpoint origin. Inspect `cause_reason` for the specific policy failure. |
| `vector_store_plaintext_secret_rejected`, `vector_store_plaintext_secret_forbidden` | Remove the value and use an approved secret reference. |
| `vector_store_secret_scheme_rejected` | Use `env://` for a host or `secretfile://` for the approved container mount. |
| `vector_store_tls_policy_violation`, `vector_store_remote_rest_tls_required`, `vector_store_remote_grpc_tls_required`, `qdrant_tls_verification_cannot_be_disabled` | Use HTTPS/gRPCS remotely and keep verification enabled. |
| `qdrant_tls_ca_cert_ref_must_be_secretfile`, `qdrant_tls_ca_requires_secure_endpoint`, `vector_store_tls_ca_cert_invalid` | Use a bounded PEM CA file under an approved secret root only with HTTPS/gRPCS; replace malformed or mismatched trust material. |
| `qdrant_grpc_url_required` | Configure and allowlist gRPC before enabling `prefer_grpc`. |
| `vector_store_domain_invalid` | Select the isolated `codecompass` or `wiki` domain. |
| `vector_store_global_default_must_be_json` | Restore JSON as the global default and opt in through an override. |
| `vector_store_override_mapping_required`, `vector_store_override_fields_forbidden`, `vector_store_qdrant_endpoint_mapping_required` | Correct the typed override body; do not add arbitrary fields. |
| `vector_store_security_policy_override_forbidden` | Move endpoint/security policy to the reviewed global Hub configuration; an override cannot widen it. |
| `vector_store_qdrant_opt_in_required` | Create an authorized profile/workspace override instead of changing the global default. |
| `vector_store_json_index_path_override_forbidden` | Remove the per-request path; JSON index locations are deployment-owned and must not be selected by callers. |
| `vector_store_override_not_found` | Resolve the active layer/revision before rollback. |
| `vector_store_override_revision_conflict` | Reload the override and retry with its current revision. |
| `vector_store_rollout_layer_invalid` | Use the `profile` or `workspace` layer. |
| `vector_store_rollout_state_invalid`, `vector_store_rollout_records_invalid` | Quarantine the malformed rollout state and restore a validated backup. |
| `vector_store_environment_boolean_invalid`, `vector_store_environment_number_invalid`, `vector_store_environment_origins_invalid` | Correct the named Hub environment variable and restart configuration loading. |
| `codecompass_vector_hub_qdrant_read_boolean_invalid` | Correct the explicit Hub-read opt-in boolean; do not infer Hub Qdrant access. |
| `codecompass_vector_runtime_scope_incomplete` | Configure all trusted CodeCompass workspace, repository and profile scope fields before enabling the runtime. |
| `vector_store_qdrant_read_execution_not_configured` | Install/configure the approved Qdrant read execution capability on the selected execution node, or keep the read disabled. |
| `wiki_vector_runtime_scope_incomplete`, `wiki_vector_hub_qdrant_read_boolean_invalid`, `wiki_vector_hub_qdrant_read_not_enabled` | Configure both trusted Wiki scope IDs and grant Hub reads only through the explicit capability overlay. |
| `wiki_qdrant_collection_prefix_must_be_separate` | Restore an `ananta-wiki` prefix; never share a CodeCompass collection namespace. |

## Endpoint and secret policy

| Code | Operator action |
| --- | --- |
| `missing_vector_store_endpoint`, `missing_vector_store_endpoint_host` | Configure an absolute endpoint with a host. |
| `invalid_vector_store_endpoint_host`, `invalid_vector_store_endpoint_port` | Correct the IDNA host or use a numeric port from 1 through 65535. |
| `unsupported_vector_store_endpoint_scheme` | Use `http`/`https` for REST or `grpc`/`grpcs` for gRPC. |
| `vector_store_endpoint_userinfo_forbidden` | Remove credentials from the URL and use a secret reference. |
| `vector_store_endpoint_query_forbidden`, `vector_store_endpoint_fragment_forbidden`, `vector_store_endpoint_path_forbidden` | Configure only the origin, without path, query or fragment. |
| `vector_store_allowed_origins_required` | Add an explicit exact allowlist. |
| `vector_store_endpoint_not_allowlisted` | Add only the reviewed exact origin or correct the endpoint. |
| `vector_store_trusted_private_origin_not_allowlisted` | Put the same private origin in the allowlist before trusting it. |
| `vector_store_external_calls_not_allowed` | Keep the request blocked or explicitly approve the reviewed remote origin. |
| `unsupported_vector_store_secret_ref` | Replace the reference with a supported scheme. |
| `invalid_vector_store_secret_ref`, `invalid_vector_store_env_secret_ref`, `invalid_vector_store_file_secret_ref` | Remove URL extras and use a valid approved environment name or absolute secret-file reference. |
| `vector_store_secret_env_names_required`, `vector_store_secret_env_not_allowed` | Configure the bounded environment-name allowlist or use the approved name. |
| `vector_store_secret_path_must_be_absolute`, `vector_store_secret_path_not_allowed`, `vector_store_secret_roots_required` | Use the exact absolute path inside an approved secret root. |
| `vector_store_secret_not_found`, `vector_store_secret_not_regular_file`, `vector_store_secret_symlink_forbidden` | Repair the secret mount; regular files only, with no symlink traversal. |
| `vector_store_secret_unreadable`, `vector_store_secret_too_large`, `vector_store_secret_empty` | Repair permissions/content and rotate a bounded non-empty key. |
| `vector_store_secret_error` | Inspect the redacted nested secret reason and repair the reference; never log the value. |

## Scope, schema and compatibility

| Code | Operator action |
| --- | --- |
| `vector_scope_required` | Supply the trusted workspace/repository/profile/domain scope. |
| `vector_scope_invalid` | Correct empty, oversized or control-character scope values. |
| `retrieval_vector_scope_reserved_ingress_forbidden` | Remove the reserved retrieval scope from generic task create/patch input. Let the Hub binding provider materialize it from authorized task/deployment context. |
| `retrieval_vector_scope_task_id_required`, `retrieval_vector_scope_task_not_found` | Bind retrieval only to an existing authoritative Hub task with a non-empty ID. |
| `retrieval_vector_scope_schema_invalid`, `retrieval_vector_scope_context_invalid`, `retrieval_vector_scope_context_fields_invalid`, `retrieval_vector_scope_binding_source_required` | Quarantine the malformed task context and rebind it through the Hub-owned provider. |
| `retrieval_vector_scope_task_binding_mismatch`, `retrieval_vector_scope_task_mismatch`, `retrieval_vector_scope_task_unbound` | Reject retrieval rather than using caller scope. Resolve the exact authoritative task binding in the Hub and retry. |
| `retrieval_vector_scope_bound_at_invalid` | Quarantine the malformed binding and rematerialize it with a finite non-negative Hub timestamp. |
| `retrieval_vector_scope_workspace_conflict`, `retrieval_vector_scope_profile_conflict`, `retrieval_vector_scope_binding_conflict` | Align the authorized CodeCompass/Wiki deployment scope or inject an explicit Hub task-to-scope provider; never choose one implicitly. |
| `retrieval_vector_scope_binding_lock_unavailable`, `retrieval_vector_scope_binding_persist_failed` | Keep vector retrieval unbound, repair Hub task mutation/persistence, then retry binding. |
| `vector_runtime_domain_invalid`, `vector_runtime_workspace_scope_required`, `vector_runtime_source_scope_required`, `vector_runtime_profile_scope_required` | Reject the read and rebuild a complete typed runtime scope from the authoritative Hub task binding. |
| `context_bundle_reserved_ingress_forbidden` | Remove caller-supplied bundle IDs or embedded context from generic task create, ingest or patch requests. Only the Hub context manager may bind persisted retrieval context. |
| `context_bundle_task_id_required`, `context_bundle_not_found`, `context_bundle_task_unbound` | Keep execution blocked and rebuild the bundle through the Hub for one concrete authoritative task. |
| `context_bundle_reference_mismatch`, `context_bundle_task_mismatch` | Reject the reference and embedded chunks; the persisted bundle belongs to another task or conflicts with the task's canonical reference. |
| `codecompass_vector_scope_domain_invalid` | Use the CodeCompass domain for CodeCompass runtime scope; do not cross the Wiki boundary. |
| `vector_scope_conflict`, `vector_index_point_scope_mismatch` | Reject the payload and regenerate it from the Hub-trusted scope. |
| `vector_point_id_mismatch` | Discard the supplied ID and regenerate the deterministic ID from trusted scope plus record ID before any network mutation. |
| `empty_query_vector`, `empty_vector`, `vector_top_k_invalid`, `vector_filter_invalid` | Correct the read-only query/vector/filter bounds. |
| `record_id_required`, `missing_source_hash`, `embedding_text_too_large` | Rebuild prepared points with required bounded metadata. |
| `vector_payload_invalid` | Reject the point before network I/O and regenerate a typed, finite, non-sensitive payload for the trusted scope. |
| `vector_payload_too_large` | Reduce the bounded metadata, labels, path, hierarchy or vector payload to the documented schema limits, then regenerate the point. |
| `dimensions_mismatch`, `distance_mismatch` | Use the active embedding/distance contract or rebuild a new version. |
| `vector_store_invalid_schema_version` | Restore the supported bounded schema identifier; do not reinterpret an unknown schema. |
| `vector_store_backend_schema_conflict` | Keep the active alias unchanged and rebuild or migrate under one consistent backend schema. |
| `provider_changed`, `model_changed`, `profile_changed`, `encoding_changed`, `config_changed`, `manifest_changed` | Treat the collection as incompatible and perform reviewed migration/rebuild. |
| `migration_required` | Run the migration dry-run; migrate only if compatible, otherwise rebuild. |
| `rebuild_required` | Recreate vectors from the authoritative source; do not activate partial state. |
| `incompatible_collection`, `vector_store_compatibility_required` | Keep the read fail-closed and supply the complete independent compatibility contract or rebuild the collection. These states must never degrade to an empty result or select JSON fallback. |
| `collection_missing` | Submit a Hub-owned rebuild for the exact scope. |
| `empty_collection`, `empty_index` | Verify whether zero records are expected; otherwise rebuild the scope. |
| `point_count_mismatch` | Keep the former alias active, discard staging and investigate the failed batch. |
| `qdrant_staging_name_exhausted` | Investigate collisions/stale staging state before retrying. |
| `migration_checkpoint_invalid` | Discard the checkpoint and restart from a dry-run; never rebind it. |
| `migration_idempotency_key_required` | Restart through the Hub task with its original non-empty idempotency key; unbound direct migrations are rejected before Qdrant writes. |
| `migration_paused` | Retry the same Hub task so its bound checkpoint is resumed. |

## Availability and fallback

| Code | Operator action |
| --- | --- |
| `qdrant_extra_required` | Install the bounded Qdrant extra in the selected worker image. |
| `qdrant_unavailable`, `qdrant_timeout` | Check readiness/network and retry according to Hub policy; fallback only if explicitly configured and compatible. |
| `qdrant_unauthorized` | Keep the read fail-closed in every availability mode, then repair or rotate the secret reference. Authorization failure must never become an empty result or select JSON fallback. |
| `qdrant_transport_timeout_unsupported` | Keep the pinned `qdrant-client` version or update its isolated adapter and compatibility tests before rollout; the client transport could not enforce the configured split connect/request timeouts. |
| `qdrant_distinct_grpc_origin_unsupported`, `qdrant_grpc_channel_already_initialized` | Disable gRPC preference and use REST, then review the pinned-client integration. |
| `fallback_not_configured` | Configure an explicit policy or fail fast; do not silently select JSON. |
| `fallback_state_incompatible` | Rebuild the JSON state for the same contract or disable fallback. |
| `vector_store_secret_resolver_required` | Inject the approved worker secret resolver; never resolve secrets in domain code. |
| `vector_store_closed` | Create a fresh store through the composition root. |
| `vector_store_observer_required` | Inject the bounded metrics observer at the composition root before recording operations. |
| `vector_store_observation_backend_invalid`, `vector_store_observation_requested_backend_invalid`, `vector_store_observation_effective_backend_invalid` | Record only a supported bounded backend identifier. |
| `vector_store_observation_operation_invalid`, `vector_store_observation_outcome_invalid` | Record only the approved low-cardinality operation/outcome values. |
| `vector_store_observation_count_invalid`, `vector_store_observation_count_key_invalid`, `vector_store_observation_duration_invalid` | Correct the non-negative bounded observation value/key; never substitute payload data. |
| `vector_store_observation_fallback_backends_required` | Record both requested and effective backends for fallback observations. |

## Task submission, authorization and queue state

| Code | Operator action |
| --- | --- |
| `vector_store_admin_required`, `vector_store_global_admin_required`, `vector_store_workspace_forbidden` | Use an appropriately scoped identity; do not widen the token. |
| `vector_store_system_authorization_purpose_invalid`, `vector_store_system_authorization_actor_invalid` | Repair the internal Hub caller so it supplies one explicit approved control-plane purpose and a non-empty audit actor; never synthesize authority from a missing request identity. |
| `vector_store_json_required`, `vector_index_request_fields_forbidden`, `vector_index_payload_fields_forbidden` | Send the documented JSON envelope and remove unknown fields. |
| `vector_index_reserved_task_ingress_forbidden` | Remove the reserved Vector-Index source, task kind or worker context from generic create/patch/orchestration-ingest input. Submit through the Hub-owned Vector-Index API. |
| `vector_index_operation_invalid`, `vector_index_search_must_not_enqueue` | Use a supported mutation; execute search through the read-only path. |
| `vector_index_workspace_id_invalid`, `vector_index_repository_id_invalid`, `vector_index_profile_name_invalid`, `vector_index_domain_invalid` | Correct the trusted scope identifiers. |
| `vector_index_idempotency_key_invalid`, `vector_index_task_idempotency_key_invalid` | Use a stable 8–128 character key matching the documented character set. |
| `vector_index_idempotency_mismatch` | Reuse a key only for the byte-equivalent intent; choose a new key for a new operation. |
| `vector_index_priority_invalid` | Use `low`, `medium`, `high` or `critical`. |
| `vector_index_task_conflict` | Wait for or cancel the active task in the same scope; do not bypass serialization. |
| `vector_index_scope_fingerprint_invalid`, `vector_index_task_scope_conflict` | Reject the malformed or concurrently changed trusted scope and reload the authoritative Hub task before retrying. |
| `vector_index_scope_fence_unavailable` | Keep submission/retry failed closed and restore the shared Hub database/advisory-lock connection; never replace it with a repository scan. |
| `vector_index_task_not_found` | Resolve the job ID from the submission response. |
| `vector_index_task_persistence_failed` | Keep the operation failed and repair Hub task persistence before retrying. |
| `vector_index_delegation_required` | Delegate the mutation through the Hub task queue; do not execute an unowned local mutation. |
| `vector_index_task_signing_keyring_required`, `vector_index_task_signing_keyring_unsafe`, `vector_index_task_signing_keyring_invalid` | Configure one secure Hub-only Ed25519 signing keyring file. Never fall back to the shared JWT secret. |
| `vector_index_task_verification_keyring_required`, `vector_index_task_verification_keyring_unsafe`, `vector_index_task_verification_keyring_invalid` | Configure the matching public-only Ed25519 verification keyring on each eligible Worker; private/symmetric material is forbidden. |
| `vector_index_task_attestation_invalid` | Reject the task before any I/O. Verify key distribution/revocation and resubmit through the Hub so the complete envelope is signed again. |
| `vector_index_task_envelope_missing`, `vector_index_task_schema_invalid`, `vector_index_task_fields_invalid`, `vector_index_task_scope_invalid`, `vector_index_task_scope_fingerprint_mismatch` | Reject the incomplete, unsupported, open-field or scope-tampered immutable Hub task envelope before execution. |
| `vector_index_task_dispatch_ttl_invalid`, `vector_index_task_dispatch_time_invalid`, `vector_index_task_dispatch_expired` | Restore a finite 30–3,600 second Hub dispatch lifetime, synchronize clocks and request a fresh attempt; never replay an expired envelope. |
| `vector_index_task_dispatch_invalid`, `vector_index_task_attempt_id_invalid`, `vector_index_task_dispatch_sequence_invalid` | Reject the malformed dispatch and have the Hub issue a fresh signed attempt. |
| `vector_index_task_dispatch_phase_invalid`, `vector_index_task_dispatch_phase_mismatch` | Use a proposal grant only for proposal and an execution grant only for execution. |
| `vector_index_task_audience_invalid`, `vector_index_task_audience_mismatch` | Align the Hub-selected Worker URL with that Worker's immutable audience; do not reuse the attempt on another Worker. |
| `vector_index_task_replay_detected` | Keep the operation blocked. Request a new Hub dispatch attempt instead of resending captured request bytes. |
| `vector_index_replay_receipt_invalid` | Reject the malformed local replay receipt before Hub admission or mutation and request a new signed dispatch. |
| `vector_index_replay_ledger_path_invalid`, `vector_index_replay_ledger_unsafe`, `vector_index_replay_ledger_unavailable` | Repair the durable Worker-private SQLite path and permissions before accepting mutations. The direct parent must be owner-only, and no non-sticky group/world-writable ancestor is allowed; do not fall back to an in-memory production ledger. |
| `vector_index_replay_retention_invalid`, `vector_index_replay_clock_invalid` | Restore a finite 3,600–31,536,000 second exact-receipt retention and a finite non-negative Worker clock. Never prune the permanent per-job sequence high-watermarks. |
| `vector_index_dispatch_worker_id_invalid`, `vector_index_dispatch_worker_identity_unavailable` | Repair the bounded Worker identity and file-managed service token before composing the mutation handler. |
| `vector_index_worker_identity_invalid`, `vector_index_worker_identity_forbidden`, `vector_index_worker_credential_reuse_denied`, `vector_index_worker_identity_configuration_invalid` | Restore one strict, active Worker registration whose advertised and authorized capabilities include `retrieval`, `index_write` and `vector_index_operation`; never reuse a Hub/session credential. |
| `vector_index_worker_role_required`, `vector_index_worker_composition_not_ready`, `vector_index_worker_capabilities_not_advertised` | Keep the Worker unready. Start it with `ROLE=worker`, repair local Vector handler composition and advertise all three required capabilities. |
| `vector_index_worker_hub_registration_disabled`, `vector_index_worker_hub_registration_pending`, `vector_index_worker_hub_registration_stale`, `vector_index_worker_hub_registration_identity_mismatch`, `vector_index_worker_hub_capabilities_incomplete` | Keep the Worker healthcheck at HTTP 503. Restore its exact file-managed identity and a fresh successful Hub registration carrying `retrieval`, `index_write` and `vector_index_operation`. |
| `vector_index_dispatch_admission_request_invalid`, `vector_index_dispatch_admission_phase_invalid`, `vector_index_dispatch_admission_sequence_invalid`, `vector_index_dispatch_admission_mismatch` | Reject the malformed or stale redemption and have the Hub issue a new signed execute attempt. |
| `vector_index_dispatch_admission_audience_mismatch`, `vector_index_dispatch_admission_expired`, `vector_index_dispatch_admission_state_invalid`, `vector_index_dispatch_admission_terminal` | Do not mutate. Restore the exact current Worker assignment or use the Hub retry transition when the task is terminal. |
| `vector_index_dispatch_admission_replay`, `vector_index_dispatch_admission_conflict` | Treat the execute grant as already consumed or concurrently revoked; never execute it again. |
| `vector_index_dispatch_admission_redirect`, `vector_index_dispatch_admission_unavailable`, `vector_index_dispatch_admission_denied` | Keep the mutation blocked and repair direct Worker-to-Hub connectivity; redirects and permissive offline fallback are forbidden. |
| `vector_index_dispatch_admission_response_invalid`, `vector_index_dispatch_admission_response_too_large` | Reject the untrusted or unbounded Hub response. A grant requires exactly HTTP 200 and a closed success envelope echoing the same job, attempt, sequence, phase and Worker audience; repair the internal endpoint or intermediary. |
| `vector_index_task_dispatch_terminal_forbidden`, `vector_index_task_dispatch_state_invalid`, `vector_index_task_dispatch_inflight`, `vector_index_task_dispatch_conflict`, `vector_index_task_dispatch_commit_invalid` | Do not issue another attempt for a terminal, invalid, admitted or concurrently changed task. Cancel/retry through the Hub when permitted. |
| `vector_index_task_domain_binding_invalid`, `vector_index_task_kind_override_forbidden`, `vector_index_task_assignment_override_forbidden`, `vector_index_task_intervention_forbidden`, `vector_index_worker_handler_unavailable` | Reject the generic execution or admin path. Repair the authoritative task-kind/envelope/Worker-handler binding and use the dedicated Vector lifecycle. Assignment may select only a Worker; it cannot replace the Hub-owned kind, capability set or credential. |
| `vector_index_task_job_id_invalid`, `vector_index_task_outer_id_mismatch`, `vector_index_task_operation_invalid`, `vector_index_task_payload_invalid`, `vector_index_task_request_fingerprint_invalid` | Reject the task and repair its signed Hub-issued outer/inner identity, operation, intent fingerprint or payload. |
| `vector_index_task_created_by_invalid`, `vector_index_task_created_at_invalid` | Reject the malformed signed audit identity/timestamp and resubmit through the Hub. |
| `vector_index_task_policy_decision_invalid`, `vector_index_task_policy_layers_invalid`, `vector_index_task_policy_layers_mismatch` | Re-evaluate rollout in the Hub and sign one closed envelope with the approved ordered policy layers. |
| `vector_index_task_resolved_config_invalid`, `vector_index_task_resolved_config_fields_invalid`, `vector_index_task_resolved_config_schema_invalid`, `vector_index_task_resolved_config_provider_invalid`, `vector_index_task_resolved_config_hash_invalid` | Re-resolve the closed immutable configuration in the Hub using the supported schema/provider/hash. |
| `vector_index_rollout_source_layers_invalid` | Submit one bounded ordered list of approved rollout source layers. |
| `vector_index_cancelled_by_hub`, `vector_index_result_after_cancel` | Do not accept late mutation results; submit a newly authorized task if still required. |
| `vector_index_cancel_state_invalid`, `vector_index_retry_state_invalid`, `vector_index_task_cas_conflict` | Apply cancel/retry only through the Hub lifecycle from an allowed authoritative state; retry a CAS conflict against a fresh task view. |
| `vector_index_goal_purge_cancel_required` | Keep the Goal and all associated rows/artifacts intact. Resolve the failed authoritative Vector lifecycle cancel, then retry the Goal purge. |
| `vector_store_request_failed`, `vector_index_operation_failed` | Inspect redacted Hub/worker diagnostics; the generic code must not be treated as success. |

## Task payload, migration input and worker result

| Code | Operator action |
| --- | --- |
| `vector_index_points_invalid`, `vector_index_inline_points_limit_exceeded`, `vector_index_point_ids_invalid`, `vector_batch_size_invalid` | Correct the typed bounded point list/batch or use a digest-bound input reference. |
| `vector_index_input_required`, `vector_index_input_ambiguous` | Supply exactly one of inline points or `input_ref`. |
| `vector_index_delete_selector_required`, `vector_index_delete_selector_ambiguous` | Supply exactly one of point IDs or `delete_all_scope`. |
| `vector_index_delete_all_scope_invalid` | Send an actual boolean and allow the Hub-authorized scope—not the worker payload—to define the deletion boundary. |
| `vector_index_batch_size_invalid` | Use a batch size from 1 through 1000. |
| `vector_index_plaintext_secret_forbidden`, `vector_index_result_plaintext_secret_forbidden` | Remove the secret from the task/result and rotate it if exposed. |
| `vector_index_migration_contract_required`, `vector_index_migration_source_required`, `vector_index_migration_requires_qdrant` | Correct the migration payload, publish one immutable source and supply its scope-bound `input_ref`; resolve the target provider to Qdrant. |
| `vector_index_migration_fields_forbidden`, `vector_index_migration_dry_run_invalid`, `vector_index_migration_max_batches_invalid` | Use only typed migration controls with valid boolean/bounds. `migration.source_path` is forbidden; the source belongs in `input_ref`. |
| `vector_index_migration_checkpoint_invalid`, `vector_index_migration_checkpoint_fields_forbidden`, `vector_index_migration_checkpoint_binding_invalid` | Resume only the complete Hub-issued checkpoint under its original scope and idempotency key. |
| `vector_index_compatibility_required`, `vector_index_compatibility_incomplete`, `vector_index_compatibility_invalid`, `vector_index_compatibility_fields_forbidden` | Supply exactly the complete typed compatibility contract and remove unknown fields. |
| `vector_index_input_ref_fields_forbidden`, `vector_index_input_ref_path_invalid`, `vector_index_input_ref_sha256_invalid`, `vector_index_input_ref_scope_fingerprint_invalid` | Use only canonical `path`, lowercase `sha256` and lowercase `scope_fingerprint` fields returned by the Hub publisher. |
| `vector_index_input_ref_sha256_required`, `vector_index_input_ref_scope_fingerprint_required`, `vector_index_input_ref_scope_required` | Add the complete Hub-published binding; workers must not open mutable or unscoped input. |
| `vector_index_input_ref_binding_invalid` | Republish and bind the canonical path, SHA-256 and full scope fingerprint as one inseparable reference. |
| `vector_index_input_ref_scope_mismatch`, `vector_index_input_ref_path_mismatch` | Reject the task before store or embedding execution. Republish under the exact trusted task scope and use the returned canonical path unchanged. |
| `vector_index_input_publisher_required`, `vector_index_input_publisher_result_invalid`, `vector_index_input_publisher_digest_mismatch` | Configure the trusted Hub artifact publisher and reject results whose path/digest does not match the published bytes. |
| `vector_index_input_publish_scope_invalid`, `vector_index_input_publish_content_invalid`, `vector_index_input_publish_content_too_large`, `vector_index_input_publish_digest_invalid`, `vector_index_input_publish_digest_mismatch` | Regenerate one bounded non-empty artifact under the trusted scope and bind it to the exact SHA-256. |
| `vector_index_input_publish_root_must_be_absolute`, `vector_index_input_publish_root_symlink_forbidden`, `vector_index_input_publish_root_unavailable`, `vector_index_input_publish_root_not_directory` | Repair the explicit Hub RW mount; use an absolute regular directory and no symlink. |
| `vector_index_input_publish_symlink_forbidden`, `vector_index_input_publish_path_escape`, `vector_index_input_publish_write_failed`, `vector_index_input_publish_existing_invalid`, `vector_index_input_publish_existing_unreadable`, `vector_index_input_publish_existing_too_large`, `vector_index_input_publish_existing_digest_mismatch` | Quarantine the artifact root, repair permissions/content and republish; never reuse unverified existing bytes. |
| `vector_index_input_ref_not_found`, `vector_index_input_ref_not_regular_file`, `vector_index_input_ref_symlink_forbidden`, `vector_index_input_ref_unreadable` | Place one immutable regular file in every eligible worker's approved root. |
| `vector_index_input_ref_too_large`, `vector_index_input_ref_digest_mismatch` | Reject the input, recopy the reviewed bounded artifact and verify its digest. |
| `vector_index_input_ref_json_invalid`, `vector_index_input_ref_points_invalid`, `vector_index_input_ref_points_limit_exceeded` | Regenerate the bounded typed JSON artifact. |
| `vector_index_input_ref_document_fields_invalid`, `vector_index_input_ref_documents_invalid`, `vector_index_input_ref_documents_required`, `vector_index_input_ref_documents_limit_exceeded` | Regenerate the versioned non-empty document bundle within the Worker limits. |
| `vector_index_input_roots_required` | Configure at least one worker-owned approved input root. |
| `vector_index_preparation_fields_forbidden`, `vector_index_preparation_schema_invalid`, `vector_index_preparation_kind_invalid`, `vector_index_preparation_profile_invalid` | Use the versioned CodeCompass/Wiki preparation schema and its exact text profile. |
| `vector_index_preparation_scope_mismatch`, `vector_index_document_input_kind_mismatch`, `vector_index_document_input_schema_invalid` | Reject the task; its document kind/schema does not match the Hub-trusted domain. |
| `vector_index_preparation_input_ref_required`, `vector_index_preparation_input_ambiguous`, `vector_index_preparation_operation_invalid` | Use one digest-bound document reference only with index, refresh or rebuild. |
| `vector_index_preparation_embedding_fields_forbidden`, `vector_index_preparation_embedding_provider_invalid`, `vector_index_preparation_embedding_dimensions_invalid`, `vector_index_preparation_embedding_timeout_invalid`, `vector_index_preparation_embedding_model_invalid` | Correct the bounded Worker embedding identity and make it equal the compatibility contract. |
| `vector_index_preparation_embedding_policy_invalid`, `vector_index_preparation_embedding_allowed_urls_invalid`, `vector_index_preparation_embedding_secret_ref_invalid` | Keep local hash offline or authorize an exact HTTPS embedding origin with a referenced Worker secret. |
| `vector_index_embedding_policy_forbidden` | Keep external embedding disabled, or select a complete exact Hub deployment profile and configure the identical canonical base URL in the immutable Worker egress allowlist. Request flags, URLs, allowlists and secret references cannot grant or widen access. |
| `vector_index_embedding_policy_profiles_json_invalid`, `vector_index_embedding_policy_profiles_mapping_required`, `vector_index_embedding_policy_profile_invalid`, `vector_index_embedding_policy_profile_fields_invalid` | Repair the immutable Hub deployment-profile JSON and use bounded unique profile mappings only. |
| `vector_index_embedding_policy_profile_domains_invalid`, `vector_index_embedding_policy_profile_embedding_invalid`, `vector_index_embedding_policy_profile_external_required`, `vector_index_embedding_policy_profile_not_normalized` | Canonicalize one explicit external profile, restrict its domains and make its complete embedding mapping pass the strict HTTPS/secret-reference policy. |
| `vector_index_embedding_plaintext_secret_forbidden`, `vector_index_embedding_config_required`, `wiki_vector_embedding_config_mismatch` | Remove plaintext credentials and provide a secret-free Worker config matching the Hub read provider. |
| `vector_store_resolved_configuration_missing` | Repair the immutable Hub task envelope; the worker must not invent configuration. |
| `vector_index_execution_adapter_unavailable` | Install/compose the configured Worker execution adapter; do not execute the mutation in the Hub. |
| `vector_index_result_schema_invalid`, `vector_index_result_fields_invalid`, `vector_index_result_status_invalid` | Reject the malformed worker result and repair the worker/gateway contract. |
| `vector_index_result_job_mismatch`, `vector_index_result_attempt_mismatch`, `vector_index_result_operation_mismatch`, `vector_index_result_idempotency_mismatch` | Reject the result; it belongs to a different task intent or an obsolete dispatch attempt. |
| `vector_index_result_dispatch_not_admitted` | Reject the result because the exact execute grant was not redeemed online at the Hub. |
| `vector_index_result_terminal_conflict`, `vector_index_result_state_invalid` | Keep the first authoritative terminal result; reject a conflicting duplicate or a result from an invalid state. |
| `vector_index_result_forwarding_fields_unknown`, `vector_index_result_recovery_task_forbidden` | Reject the generic or Recovery forwarding payload; only the closed Vector result schema may enter this domain. |
| `vector_index_result_reason_invalid`, `vector_index_result_error_invalid` | Return bounded string diagnostics without payload or secret content. |
| `vector_index_result_status_values_forbidden`, `vector_index_worker_result_status_invalid`, `vector_index_result_verification_invalid` | Reject unsupported worker status/result verification data and repair the worker gateway contract. |
| `vector_index_result_mapping_invalid`, `vector_index_result_key_invalid`, `vector_index_result_value_invalid`, `vector_index_result_number_invalid` | Return only finite bounded JSON objects from the Worker. |
| `vector_index_result_depth_limit_exceeded`, `vector_index_result_entry_limit_exceeded`, `vector_index_result_list_limit_exceeded`, `vector_index_result_key_limit_exceeded`, `vector_index_result_string_limit_exceeded`, `vector_index_result_size_limit_exceeded` | Reduce diagnostics to the bounded result projection; never persist arbitrary tool output. |
| `vector_index_result_sensitive_key_forbidden`, `vector_index_result_sensitive_value_forbidden`, `vector_index_result_document_content_forbidden` | Reject and rotate any exposed credential; remove documents, prompts, outputs, embeddings and vectors from results. |
