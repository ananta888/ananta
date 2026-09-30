import type {
  SemanticComputeCandidateClaim,
  SemanticComputeCapabilityGrantReadModel,
  SemanticComputeContractReadModel,
  SemanticComputeExplanationReadModel,
  SemanticComputeLeaseReadModel,
  SemanticComputeProfile,
  SemanticComputeSuggestionReadModel,
  SemanticTaskLeaseReadModel,
} from "./semantic-compute-contract.models";
import {
  boundedInteger,
  boundedText,
  digest,
  enumValue,
  exactRecord,
  exactRecordWithOptional,
  executorIdentifier,
  finiteNumber,
  identifier,
  nonnegativeInteger,
  parseBudget,
  positiveInteger,
  record,
  stringArray,
} from "./semantic-compute-contract.values";

/**
 * Strict parsers from Hub JSON to frozen semantic compute read models:
 * exact field sets, bounded values and consistent timing/sequence windows.
 */

const CONTRACT_READ_FIELDS = [
  "contract_id",
  "session_id",
  "room_id",
  "epoch",
  "revision",
  "digest",
  "status",
  "profile",
  "quality_level",
  "security_mode",
  "consent_version",
  "policy_version",
  "delay_ms",
  "roles",
  "task_types",
  "max_artifact_bytes",
  "deadline_ms",
  "expires_at_ms",
  "reason_code",
] as const;
const LEASE_READ_FIELDS = [
  "lease_id",
  "contract_id",
  "contract_digest",
  "session_id",
  "epoch",
  "task_type",
  "audience",
  "role",
  "executor_id",
  "sequence_start",
  "sequence_end",
  "fencing_token",
  "resource_budget",
  "status",
  "issued_at_ms",
  "expires_at_ms",
  "deadline_at_ms",
  "version",
  "authoritative_source",
] as const;
const TASK_LEASE_FIELDS = [
  "schema",
  "lease_id",
  "contract_id",
  "contract_digest",
  "session_id",
  "epoch",
  "task_type",
  "role",
  "executor_id",
  "audience",
  "sequence_start",
  "sequence_end",
  "fencing_token",
  "resource_budget",
  "issued_at_ms",
  "expires_at_ms",
  "deadline_ms",
  "issuer",
  "signature",
] as const;

export function parseSemanticComputeContract(
  value: unknown,
  nowMs?: number,
): SemanticComputeContractReadModel {
  const row = exactRecord(
    value,
    CONTRACT_READ_FIELDS,
    "semantic_compute_contract_shape_invalid",
  );
  const status = enumValue(row["status"], [
    "offered",
    "countered",
    "accepted",
    "active",
    "revoked",
    "fallback",
  ] as const);
  const profile = enumValue(row["profile"], [
    "off",
    "conservative",
    "balanced",
    "custom",
  ] as const);
  const quality = enumValue(row["quality_level"], [
    "best_effort",
    "standard",
    "verified",
  ] as const);
  const security = enumValue(row["security_mode"], [
    "strict_e2ee",
    "trusted_compute",
  ] as const);
  const roles = record(row["roles"]);
  if (
    Object.keys(roles).some(
      (name) => !["primary", "validator", "standby"].includes(name),
    )
  ) {
    throw new Error("semantic_compute_roles_invalid");
  }
  const parseRole = (name: string): readonly string[] | undefined => {
    const value = roles[name];
    if (value === undefined) return undefined;
    if (!Array.isArray(value) || value.length > 2)
      throw new Error("semantic_compute_roles_invalid");
    return Object.freeze(value.map(identifier));
  };
  const expiresAtMs = positiveInteger(row["expires_at_ms"]);
  if (nowMs !== undefined && expiresAtMs <= nowMs)
    throw new Error("semantic_compute_contract_expired");
  return Object.freeze({
    contract_id: identifier(row["contract_id"]),
    session_id: identifier(row["session_id"]),
    room_id: row["room_id"] == null ? null : identifier(row["room_id"]),
    epoch: positiveInteger(row["epoch"]),
    revision: positiveInteger(row["revision"]),
    digest: digest(row["digest"]),
    status,
    profile,
    quality_level: quality,
    security_mode: security,
    consent_version: nonnegativeInteger(row["consent_version"]),
    policy_version: identifier(row["policy_version"]),
    delay_ms: boundedInteger(row["delay_ms"], 2_000, 20_000),
    roles: Object.freeze({
      primary: parseRole("primary"),
      validator: parseRole("validator"),
      standby: parseRole("standby"),
    }),
    task_types: Object.freeze(stringArray(row["task_types"], 8)),
    max_artifact_bytes: boundedInteger(
      row["max_artifact_bytes"],
      1_024,
      4_194_304,
    ),
    deadline_ms: boundedInteger(row["deadline_ms"], 100, 20_000),
    expires_at_ms: expiresAtMs,
    reason_code:
      row["reason_code"] == null ? null : identifier(row["reason_code"]),
  });
}

export function parseSemanticComputeLease(
  value: unknown,
  nowMs?: number,
): SemanticComputeLeaseReadModel {
  const row = exactRecordWithOptional(
    value,
    LEASE_READ_FIELDS,
    ["task_lease"],
    "semantic_compute_lease_shape_invalid",
  );
  const expiresAtMs = positiveInteger(row["expires_at_ms"]);
  if (nowMs !== undefined && expiresAtMs <= nowMs)
    throw new Error("semantic_compute_lease_expired");
  const taskLease =
    row["task_lease"] === undefined
      ? undefined
      : parseSemanticTaskLease(row["task_lease"], nowMs);
  if (
    taskLease !== undefined &&
    (taskLease.lease_id !== row["lease_id"] ||
      taskLease.contract_id !== row["contract_id"] ||
      taskLease.contract_digest !== row["contract_digest"] ||
      taskLease.session_id !== row["session_id"] ||
      taskLease.epoch !== row["epoch"] ||
      taskLease.task_type !== row["task_type"] ||
      taskLease.audience !== row["audience"] ||
      taskLease.role !== row["role"] ||
      taskLease.executor_id !== row["executor_id"] ||
      taskLease.sequence_start !== row["sequence_start"] ||
      taskLease.sequence_end !== row["sequence_end"] ||
      taskLease.fencing_token !== row["fencing_token"])
  )
    throw new Error("semantic_compute_task_lease_binding_invalid");
  if (row["authoritative_source"] !== "hub")
    throw new Error("semantic_compute_lease_authority_invalid");
  return Object.freeze({
    lease_id: identifier(row["lease_id"]),
    contract_id: identifier(row["contract_id"]),
    contract_digest: digest(row["contract_digest"]),
    session_id: identifier(row["session_id"]),
    epoch: positiveInteger(row["epoch"]),
    task_type: identifier(row["task_type"]),
    audience: identifier(row["audience"]),
    role: enumValue(row["role"], ["primary", "validator", "standby"] as const),
    executor_id: executorIdentifier(row["executor_id"]),
    sequence_start: nonnegativeInteger(row["sequence_start"]),
    sequence_end: nonnegativeInteger(row["sequence_end"]),
    fencing_token: positiveInteger(row["fencing_token"]),
    resource_budget: Object.freeze(parseBudget(row["resource_budget"])),
    status: enumValue(row["status"], ["active", "expired", "revoked"] as const),
    issued_at_ms: positiveInteger(row["issued_at_ms"]),
    expires_at_ms: expiresAtMs,
    deadline_at_ms: positiveInteger(row["deadline_at_ms"]),
    version: positiveInteger(row["version"]),
    authoritative_source: "hub" as const,
    ...(taskLease === undefined ? {} : { task_lease: taskLease }),
  });
}

export function parseSemanticTaskLease(
  value: unknown,
  nowMs?: number,
): SemanticTaskLeaseReadModel {
  const row = exactRecordWithOptional(
    value,
    TASK_LEASE_FIELDS,
    ["room_id"],
    "semantic_compute_task_lease_shape_invalid",
  );
  if (
    row["schema"] !== "ananta.semantic-task-lease.v1" ||
    row["issuer"] !== "hub"
  ) {
    throw new Error("semantic_compute_task_lease_authority_invalid");
  }
  const sequenceStart = nonnegativeInteger(row["sequence_start"]);
  const sequenceEnd = nonnegativeInteger(row["sequence_end"]);
  if (sequenceEnd < sequenceStart || sequenceEnd - sequenceStart > 10_000) {
    throw new Error("semantic_compute_task_lease_sequence_invalid");
  }
  const issuedAtMs = positiveInteger(row["issued_at_ms"]);
  const expiresAtMs = positiveInteger(row["expires_at_ms"]);
  if (expiresAtMs <= issuedAtMs || expiresAtMs - issuedAtMs > 300_000) {
    throw new Error("semantic_compute_task_lease_window_invalid");
  }
  if (nowMs !== undefined && expiresAtMs <= nowMs)
    throw new Error("semantic_compute_task_lease_expired");
  const signature = exactRecord(
    row["signature"],
    ["algorithm", "key_id", "value"],
    "semantic_compute_task_lease_signature_invalid",
  );
  if (
    signature["algorithm"] !== "hmac-sha256" ||
    !/^[a-f0-9]{64}$/.test(String(signature["value"] ?? ""))
  ) {
    throw new Error("semantic_compute_task_lease_signature_invalid");
  }
  return Object.freeze({
    schema: "ananta.semantic-task-lease.v1" as const,
    lease_id: identifier(row["lease_id"]),
    contract_id: identifier(row["contract_id"]),
    contract_digest: digest(row["contract_digest"]),
    session_id: identifier(row["session_id"]),
    ...(row["room_id"] === undefined
      ? {}
      : { room_id: identifier(row["room_id"]) }),
    epoch: positiveInteger(row["epoch"]),
    task_type: identifier(row["task_type"]),
    role: enumValue(row["role"], ["primary", "validator", "standby"] as const),
    executor_id: executorIdentifier(row["executor_id"]),
    audience: identifier(row["audience"]),
    sequence_start: sequenceStart,
    sequence_end: sequenceEnd,
    fencing_token: positiveInteger(row["fencing_token"]),
    resource_budget: Object.freeze(parseBudget(row["resource_budget"])),
    issued_at_ms: issuedAtMs,
    expires_at_ms: expiresAtMs,
    deadline_ms: boundedInteger(row["deadline_ms"], 1, 20_000),
    issuer: "hub" as const,
    signature: Object.freeze({
      algorithm: "hmac-sha256" as const,
      key_id: identifier(signature["key_id"]),
      value: String(signature["value"]),
    }),
  });
}

export function parseSemanticComputeCapabilityGrant(
  value: unknown,
): SemanticComputeCapabilityGrantReadModel {
  const row = record(value);
  const expiresAtSeconds = finiteNumber(
    row["expires_at"],
    1,
    Number.MAX_SAFE_INTEGER / 1_000,
  );
  const revokedAt = row["revoked_at"];
  if (revokedAt !== null && revokedAt !== undefined)
    finiteNumber(revokedAt, 0, Number.MAX_SAFE_INTEGER / 1_000);
  return Object.freeze({
    grant_id: identifier(row["grant_id"]),
    subject_id: identifier(row["subject_id"]),
    capability: enumValue(row["capability"], [
      "publish",
      "subscribe",
      "compute",
      "validate",
    ] as const),
    scope_kind: enumValue(row["scope_kind"], ["session", "room"] as const),
    scope_id: identifier(row["scope_id"]),
    direction: enumValue(row["direction"], [
      "ingress",
      "egress",
      "bidirectional",
    ] as const),
    epoch: positiveInteger(row["epoch"]),
    expires_at_ms: Math.floor(expiresAtSeconds * 1_000),
    revoked: revokedAt !== null && revokedAt !== undefined,
  });
}

export function parseCandidateClaim(value: unknown): SemanticComputeCandidateClaim {
  const row = record(value);
  const profile = record(row["resource_profile"]);
  const normalized: Record<string, string> = {};
  for (const name of ["cpu", "memory", "gpu", "codec", "battery", "network"]) {
    if (typeof profile[name] !== "string" || String(profile[name]).length > 32)
      throw new Error("semantic_compute_claim_invalid");
    normalized[name] = String(profile[name]);
  }
  if (row["authoritative"] !== false)
    throw new Error("semantic_compute_claim_authority_invalid");
  return Object.freeze({
    advertisement_id: identifier(row["advertisement_id"]),
    sender_id: identifier(row["sender_id"]),
    resource_profile: Object.freeze(normalized),
    expires_at_ms: positiveInteger(row["expires_at_ms"]),
    authoritative: false as const,
  });
}

export function parseExplanation(value: unknown): SemanticComputeExplanationReadModel {
  const row = record(value);
  if (row["authoritative_source"] !== "hub")
    throw new Error("semantic_compute_explanation_authority_invalid");
  const message = boundedText(row["message"], 1, 512);
  return Object.freeze({
    state: identifier(row["state"]),
    reason_code: identifier(row["reason_code"]),
    message,
    revision: positiveInteger(row["revision"]),
    contract_digest: digest(row["contract_digest"]),
    profile: enumValue(row["profile"], [
      "off",
      "conservative",
      "balanced",
      "custom",
    ] as const),
    delay_ms: boundedInteger(row["delay_ms"], 2_000, 20_000),
    authoritative_source: "hub" as const,
  });
}

export function parseSuggestion(value: unknown): SemanticComputeSuggestionReadModel {
  const row = record(value);
  if (
    row["authoritative"] !== false ||
    row["requires_separate_hub_mutation"] !== true
  ) {
    throw new Error("semantic_compute_suggestion_authority_invalid");
  }
  const rawValues = record(row["suggested_values"]);
  if (
    Object.keys(rawValues).some((key) => !["profile", "delay_ms"].includes(key))
  ) {
    throw new Error("semantic_compute_suggestion_field_invalid");
  }
  const suggestedValues: {
    profile?: SemanticComputeProfile;
    delay_ms?: number;
  } = {};
  if (rawValues["profile"] !== undefined) {
    suggestedValues.profile = enumValue(rawValues["profile"], [
      "off",
      "conservative",
      "balanced",
      "custom",
    ] as const);
  }
  if (rawValues["delay_ms"] !== undefined) {
    suggestedValues.delay_ms = boundedInteger(
      rawValues["delay_ms"],
      2_000,
      20_000,
    );
  }
  return Object.freeze({
    authoritative: false as const,
    requires_separate_hub_mutation: true as const,
    suggested_values: Object.freeze(suggestedValues),
    rationale: boundedText(row["rationale"], 0, 2_048),
  });
}

export function contractFromResponse(
  value: unknown,
): SemanticComputeContractReadModel {
  const response = record(value);
  return parseSemanticComputeContract(response["contract"] ?? response["data"]);
}

export function capabilityGrantFromResponse(
  value: unknown,
): SemanticComputeCapabilityGrantReadModel {
  const response = record(value);
  return parseSemanticComputeCapabilityGrant(
    response["grant"] ?? response["data"],
  );
}
