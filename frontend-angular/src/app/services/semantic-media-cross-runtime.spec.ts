import catalog from "../../../../tests/fixtures/semantic_media_contracts/catalog.v1.json";
import domainVectors from "../../../../tests/fixtures/semantic_media_contracts/domain-vectors.v1.json";

import { parseCapabilityAdvertisement } from "./peer-capability.service";
import {
  parseSemanticComputeContract,
  parseSemanticComputeLease,
} from "./semantic-compute-contract-api.service";
import { parseSemanticScene } from "./semantic-scene-model";
import { validateSemanticSpeechPayload } from "./semantic-speech-transport.service";
import {
  canonicalJson,
  sha256Canonical,
  validateSpeechEvidenceMessage,
} from "./speech-evidence-sync.validators";
import { parseSpeechAdapterEnvelope } from "./speech-adapter-registry-api.service";
import { validateSpeechReconciliationCreateRequest } from "./speech-reconciliation-api.service";
import {
  canonicalSecurityJson,
  parseSecureEnvelope,
} from "./webrtc-secure-envelope";

const DIGEST = "a".repeat(64);
type Projection = Record<string, unknown>;

function finiteProjection(raw: Projection): Projection {
  return Object.fromEntries(
    Object.entries(raw).map(([key, value]) => [
      key,
      value === "__NON_FINITE__" ? Number.NaN : value,
    ]),
  );
}

function extras(raw: Projection, admitted: readonly string[]): Projection {
  const names = new Set(admitted);
  return Object.fromEntries(
    Object.entries(raw).filter(([key]) => !names.has(key)),
  );
}

function envelope(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const payload = {
    version: 1,
    scope: { kind: "session", id: "session-a" },
    sender_id: "peer-a",
    recipient: { kind: "peer", id: "peer-b" },
    epoch: 1,
    sequence: value["sequence"],
    key_id: "key-a",
    payload_type: "semantic.scene",
    expires_at_ms: value["expires_at_ms"],
    nonce_b64: btoa("\0".repeat(12)),
    aad: {
      traffic_class: "semantic",
      content_encoding: "json",
      contract_digest: DIGEST,
    },
    ciphertext_b64: btoa("\0".repeat(16)),
    ...extras(value, ["sequence", "expires_at_ms"]),
  };
  canonicalSecurityJson(payload);
  parseSecureEnvelope(payload, { nowMs });
}

function permission(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const expiry = value["measurements_expires_at_ms"];
  const payload = {
    schema: "ananta.semantic-capability-advertisement.v1",
    advertisement_id: "capability-a",
    session_id: "session-a",
    epoch: 1,
    sender_id: "peer-a",
    algorithms: ["heuristic-visual-v1"],
    roles: ["executor"],
    task_types: ["visual_extract"],
    resource_profile: {
      cpu: "medium",
      memory: "medium",
      gpu: "none",
      codec: "software",
      battery: "mains",
      network: "normal",
    },
    measurements_expires_at_ms: expiry,
    expires_at_ms: expiry,
    max_delay_ms: value["max_delay_ms"],
    max_artifact_bytes: 1024,
    signature: {
      algorithm: "ed25519",
      key_id: "key-a",
      value: "signed-value-0001",
    },
    ...extras(value, ["max_delay_ms", "measurements_expires_at_ms"]),
  };
  canonicalJson(payload);
  parseCapabilityAdvertisement(payload, nowMs);
}

function contract(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const payload = {
    contract_id: "contract-a",
    session_id: "session-a",
    room_id: null,
    epoch: 1,
    revision: 1,
    digest: DIGEST,
    status: "active",
    profile: "balanced",
    quality_level: "standard",
    security_mode: "strict_e2ee",
    consent_version: 1,
    policy_version: "policy-v1",
    delay_ms: 2_000,
    roles: { primary: ["peer-a"] },
    task_types: ["visual_extract"],
    max_artifact_bytes: 1024,
    deadline_ms: value["deadline_ms"],
    expires_at_ms: value["expires_at_ms"],
    reason_code: null,
    ...extras(value, ["deadline_ms", "expires_at_ms"]),
  };
  canonicalJson(payload);
  parseSemanticComputeContract(payload, nowMs);
}

function lease(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const expiry = value["expires_at_ms"];
  const issuedAt =
    typeof expiry === "number" && Number.isSafeInteger(expiry)
      ? Math.min(nowMs, expiry - 1)
      : nowMs;
  const payload = {
    lease_id: "lease-a",
    contract_id: "contract-a",
    contract_digest: DIGEST,
    session_id: "session-a",
    epoch: 1,
    task_type: "visual_extract",
    audience: "worker-a",
    role: "primary",
    executor_id: value["executor_id"] ?? "worker-a",
    sequence_start: 0,
    sequence_end: 1,
    fencing_token: value["fencing_token"],
    resource_budget: { cpu_ms: 1, memory_bytes: 1_048_576, artifact_bytes: 1 },
    status: "active",
    issued_at_ms: issuedAt,
    expires_at_ms: expiry,
    deadline_at_ms: expiry,
    version: 1,
    authoritative_source: "hub",
    ...extras(value, ["fencing_token", "expires_at_ms", "executor_id"]),
  };
  canonicalJson(payload);
  parseSemanticComputeLease(payload, nowMs);
}

function scene(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const payload = {
    schema: "ananta.semantic-scene.v1",
    scene_id: "scene-a",
    session_id: "session-a",
    contract_id: "contract-a",
    contract_digest: DIGEST,
    epoch: 1,
    sequence: value["sequence"],
    source_frame_digest: DIGEST,
    coordinate_space: {
      unit: "normalized",
      origin: "top_left",
      width: 1,
      height: 1,
    },
    timebase: {
      unit: "milliseconds",
      captured_at_ms: value["captured_at_ms"],
      duration_ms: 0,
    },
    provenance: {
      source: "heuristic",
      algorithm: "standard",
      version: "v1",
      authoritative: false,
    },
    nodes: [],
    security: {
      classification: "derived_semantic_metadata",
      raw_media_included: false,
    },
    ...extras(value, ["sequence", "captured_at_ms"]),
  };
  canonicalJson(payload);
  parseSemanticScene(payload, nowMs);
}

function speech(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const payload = {
    version: "ananta.semantic-speech.v1",
    kind: "transcript_revision",
    session_id: "session-a",
    epoch: 1,
    turn_id: "turn-a",
    revision: value["revision"],
    sender_id: "peer-a",
    audience_id: "peer-b",
    consent_version: 1,
    expires_at_ms: value["expires_at_ms"],
    contract_digest: DIGEST,
    source_digest: null,
    authority: "final",
    text: "deterministic fixture",
    ...extras(value, ["revision", "expires_at_ms"]),
  };
  canonicalJson(payload);
  validateSemanticSpeechPayload(
    payload,
    {
      sessionId: "session-a",
      epoch: 1,
      localPeerId: "peer-b",
      remotePeerId: "peer-a",
      consentVersion: 1,
      contractDigest: DIGEST,
    },
    nowMs,
  );
}

function evidence(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const expiry = value["expires_at_ms"];
  const issuedAt =
    typeof expiry === "number" && Number.isSafeInteger(expiry)
      ? Math.min(nowMs, expiry - 1)
      : nowMs;
  const payloadBody = {
    traffic_class: "control",
    inventory_id: "inventory-a",
    root_digest: DIGEST,
    leaf_count: 0,
    total_bytes: 0,
    scope_digest: DIGEST,
    retention_until_ms: nowMs + 1,
    cursor_digest: DIGEST,
  };
  const payload = {
    protocol_version: "ananta.speech-evidence-sync.v1",
    message_type: "inventory",
    message_id: "message-a",
    session_id: "session-a",
    pair_id: "pair-a",
    sender_id: "peer-a",
    audience_id: "peer-b",
    epoch: 1,
    sequence: value["sequence"],
    consent_version: 1,
    key_id: "key-a",
    issued_at_ms: issuedAt,
    expires_at_ms: expiry,
    payload_digest: DIGEST,
    payload: payloadBody,
    signature_algorithm: "Ed25519",
    signature_b64: btoa("\0".repeat(64)),
    ...extras(value, ["sequence", "expires_at_ms"]),
  };
  canonicalJson(payload);
  validateSpeechEvidenceMessage(payload, nowMs);
}

function reconciliation(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const payload = {
    consent_id: "consent-a",
    consent_version: value["consent_version"],
    revocation_epoch: 0,
    input_manifest_digest: DIGEST,
    policy_digest: DIGEST,
    research_policy_ref: null,
    max_compute_factor: 1,
    key_epoch: 1,
    deadline_at_ms: value["deadline_at_ms"],
    resource_limits: {
      wall_time_ms: 1,
      cpu_time_ms: 0,
      gpu_time_ms: 0,
      memory_byte_ms: 0,
      disk_bytes: 0,
      checkpoint_bytes: 0,
      energy_millijoules: 0,
    },
    ...extras(value, ["consent_version", "deadline_at_ms"]),
  };
  canonicalJson(payload);
  validateSpeechReconciliationCreateRequest(payload as never, nowMs);
}

function training(raw: Projection, nowMs: number): void {
  const value = finiteProjection(raw);
  const expiry = value["consent_expires_at_ms"];
  const metadata = {
    adapter_id: "adapter-a",
    version: "v1",
    pair_id: "pair-a",
    direction: "sender_to_receiver",
    speaker_digest: DIGEST,
    scope_digest: DIGEST,
    base_model_id: "base-a",
    base_model_digest: DIGEST,
    backend: "mock",
    backend_digest: DIGEST,
    dataset_digest: DIGEST,
    split_digest: DIGEST,
    evaluation_report_digest: DIGEST,
    evaluation_policy_version: "policy-v1",
    consent_digest: DIGEST,
    consent_expires_at_ms: expiry,
    artifact_ref: "artifact://speech-adapters/test/adapter-a",
    artifact_sha256: DIGEST,
    artifact_size_bytes: value["artifact_size_bytes"],
    expires_at_ms: expiry,
    status: "approved",
    registry_version: 1,
    approval_reason_code: "approved",
    approved_at_ms: 1,
    revoked_at_ms: null,
    deprecated_at_ms: null,
    expired_at_ms: null,
    rollback_of_adapter_id: null,
    created_at_ms: 1,
    updated_at_ms: 1,
    lineage: [],
    ...extras(value, ["artifact_size_bytes", "consent_expires_at_ms"]),
  };
  canonicalJson(metadata);
  parseSpeechAdapterEnvelope({ ok: true, data: metadata }, nowMs);
}

const productionAdapters: Record<
  string,
  (raw: Projection, nowMs: number) => void
> = {
  envelope,
  permission,
  contract,
  lease,
  scene,
  speech,
  evidence,
  reconciliation,
  training,
};

const reasonCompatibility: Record<string, Record<string, string>> = {
  envelope: {
    unknown_field: "unknown_field",
    sequence_invalid: "integer_out_of_bounds",
    expired: "stale_time",
    non_finite_or_unserializable: "non_finite",
  },
  permission: {
    invalid_capability: "unknown_field",
    impossible_budget: "integer_out_of_bounds",
    capability_expired: "stale_time",
  },
  contract: {
    semantic_compute_contract_shape_invalid: "unknown_field",
    semantic_compute_integer_invalid: "integer_out_of_bounds",
    semantic_compute_contract_expired: "stale_time",
  },
  lease: {
    semantic_compute_lease_shape_invalid: "unknown_field",
    semantic_compute_integer_invalid: "integer_out_of_bounds",
    semantic_compute_lease_expired: "stale_time",
    speech_evidence_non_finite: "non_finite",
    semantic_compute_executor_id_invalid: "unsafe_executor",
  },
  scene: {
    invalid_scene: "unknown_field",
    invalid_integer: "integer_out_of_bounds",
    stale_scene: "stale_time",
    speech_evidence_non_finite: "non_finite",
  },
  speech: {
    semantic_speech_unknown_field: "unknown_field",
    semantic_speech_context_mismatch: "context_mismatch",
  },
  evidence: {
    speech_evidence_unknown_field: "unknown_field",
    speech_evidence_sequence_invalid: "integer_out_of_bounds",
    speech_evidence_expired: "stale_time",
  },
  reconciliation: {
    speech_reconciliation_create_request_shape_invalid: "unknown_field",
    speech_reconciliation_consent_version_invalid: "integer_out_of_bounds",
    speech_reconciliation_deadline_at_ms_invalid: "stale_time",
  },
  training: {
    speech_adapter_metadata_invalid: "unknown_field",
    speech_adapter_artifact_size_invalid: "integer_out_of_bounds",
    speech_adapter_expired: "stale_time",
    speech_evidence_non_finite: "non_finite",
  },
};

function admit(
  domain: string,
  raw: Projection,
  nowMs: number,
): { accepted: boolean; reason_code: string } {
  try {
    productionAdapters[domain](raw, nowMs);
    return { accepted: true, reason_code: "accepted" };
  } catch (error) {
    const reason = String(
      (error as { reasonCode?: unknown }).reasonCode ??
        (error as Error).message,
    );
    return {
      accepted: false,
      reason_code: reasonCompatibility[domain][reason] ?? `unmapped:${reason}`,
    };
  }
}

describe("semantic media cross-runtime production parser catalog", () => {
  it("uses the same complete domain catalog as Python and affected workers", () => {
    expect(catalog.domains.map((domain) => domain.name).sort()).toEqual(
      Object.keys(productionAdapters).sort(),
    );
    expect(
      catalog.domains.every(
        (domain) =>
          domain.cases.includes("valid") &&
          domain.cases.includes("unknown-field"),
      ),
    ).toBe(true);
  });

  it("matches UTF-8 canonical JSON, hashing and maximum safe integer semantics", async () => {
    expect(canonicalJson(catalog.canonical_utf8.value)).toBe(
      catalog.canonical_utf8.json,
    );
    await expect(sha256Canonical(catalog.canonical_utf8.value)).resolves.toBe(
      catalog.canonical_utf8.sha256,
    );
    expect(catalog.canonical_utf8.value.bounds.maximum).toBe(
      Number.MAX_SAFE_INTEGER,
    );
    for (const vector of catalog.vectors) {
      expect(canonicalJson(vector.input)).toBe(vector.canonical_json);
      await expect(sha256Canonical(vector.input)).resolves.toBe(vector.sha256);
    }
  });

  it("executes every positive and negative vector through its production parser", () => {
    expect(domainVectors.domains.map((row) => row.name).sort()).toEqual(
      Object.keys(productionAdapters).sort(),
    );
    let negatives = 0;
    for (const row of domainVectors.domains) {
      for (const vector of row.vectors) {
        const actual = admit(
          row.name,
          vector.input as Projection,
          domainVectors.reference_clock_ms,
        );
        expect(actual, vector.id).toEqual(vector.expected);
        if (!vector.expected.accepted) negatives += 1;
      }
    }
    expect(negatives).toBeGreaterThanOrEqual(30);
  });

  it("keeps unknown-field, bound and stale-time negative mutations in every domain", () => {
    for (const row of domainVectors.domains) {
      const cases = new Set(
        row.vectors
          .filter((vector) => !vector.expected.accepted)
          .map((vector) => vector.case),
      );
      expect(cases.has("unknown-field"), row.name).toBe(true);
      expect(cases.has("integer-overflow"), row.name).toBe(true);
      expect(cases.has("stale-time"), row.name).toBe(true);
    }
  });
});
