import { Injectable, inject } from "@angular/core";
import { Observable, map } from "rxjs";

import { HubApiCoreService } from "./hub-api-core.service";

export type SemanticComputeProfile =
  "off" | "conservative" | "balanced" | "custom";
export type SemanticComputeAction =
  "counter" | "accept" | "activate" | "revoke" | "fallback";

export interface SemanticComputeContractReadModel {
  readonly contract_id: string;
  readonly session_id: string;
  readonly room_id: string | null;
  readonly epoch: number;
  readonly revision: number;
  readonly digest: string;
  readonly status:
    "offered" | "countered" | "accepted" | "active" | "revoked" | "fallback";
  readonly profile: SemanticComputeProfile;
  readonly quality_level: "best_effort" | "standard" | "verified";
  readonly security_mode: "strict_e2ee" | "trusted_compute";
  readonly consent_version: number;
  readonly policy_version: string;
  readonly delay_ms: number;
  readonly roles: Readonly<{
    primary?: readonly string[];
    validator?: readonly string[];
    standby?: readonly string[];
  }>;
  readonly task_types: readonly string[];
  readonly max_artifact_bytes: number;
  readonly deadline_ms: number;
  readonly expires_at_ms: number;
  readonly reason_code: string | null;
}

export interface SemanticComputeLeaseReadModel {
  readonly lease_id: string;
  readonly contract_id: string;
  readonly contract_digest: string;
  readonly session_id: string;
  readonly epoch: number;
  readonly task_type: string;
  readonly audience: string;
  readonly role: "primary" | "validator" | "standby";
  readonly executor_id: string;
  readonly sequence_start: number;
  readonly sequence_end: number;
  readonly fencing_token: number;
  readonly resource_budget: Readonly<{
    cpu_ms: number;
    memory_bytes: number;
    artifact_bytes: number;
  }>;
  readonly status: "active" | "expired" | "revoked";
  readonly issued_at_ms: number;
  readonly expires_at_ms: number;
  readonly deadline_at_ms: number;
  readonly version: number;
  readonly authoritative_source: "hub";
  readonly task_lease?: SemanticTaskLeaseReadModel;
}

export interface SemanticTaskLeaseReadModel {
  readonly schema: "ananta.semantic-task-lease.v1";
  readonly lease_id: string;
  readonly contract_id: string;
  readonly contract_digest: string;
  readonly session_id: string;
  readonly room_id?: string;
  readonly epoch: number;
  readonly task_type: string;
  readonly role: "primary" | "validator" | "standby";
  readonly executor_id: string;
  readonly audience: string;
  readonly sequence_start: number;
  readonly sequence_end: number;
  readonly fencing_token: number;
  readonly resource_budget: Readonly<{
    cpu_ms: number;
    memory_bytes: number;
    artifact_bytes: number;
  }>;
  readonly issued_at_ms: number;
  readonly expires_at_ms: number;
  readonly deadline_ms: number;
  readonly issuer: "hub";
  readonly signature: Readonly<{
    algorithm: "hmac-sha256";
    key_id: string;
    value: string;
  }>;
}

export interface SemanticComputeCandidateClaim {
  readonly advertisement_id: string;
  readonly sender_id: string;
  readonly resource_profile: Readonly<Record<string, string>>;
  readonly expires_at_ms: number;
  readonly authoritative: false;
}

export interface SemanticComputeExplanationReadModel {
  readonly state: string;
  readonly reason_code: string;
  readonly message: string;
  readonly revision: number;
  readonly contract_digest: string;
  readonly profile: SemanticComputeProfile;
  readonly delay_ms: number;
  readonly authoritative_source: "hub";
}

export interface SemanticComputeSuggestionReadModel {
  readonly authoritative: false;
  readonly requires_separate_hub_mutation: true;
  readonly suggested_values: Readonly<{
    profile?: SemanticComputeProfile;
    delay_ms?: number;
  }>;
  readonly rationale: string;
}

export interface SemanticComputeOfferRequest {
  readonly sessionId: string;
  readonly roomId?: string;
  readonly epoch: number;
  readonly policyVersion: string;
  readonly consentVersion: number;
  readonly proposal: Readonly<Record<string, unknown>>;
  readonly advertisements: readonly Readonly<Record<string, unknown>>[];
}

export interface SemanticComputeMutationRequest {
  readonly sessionId: string;
  readonly epoch: number;
  readonly expectedRevision: number;
  readonly consentVersion: number;
  readonly proposal?: Readonly<Record<string, unknown>>;
  readonly advertisements?: readonly Readonly<Record<string, unknown>>[];
}

export type SemanticComputeCapability =
  "publish" | "subscribe" | "compute" | "validate";
export type SemanticComputeCapabilityDirection = "ingress" | "egress";

export interface SemanticComputeCapabilityGrantReadModel {
  readonly grant_id: string;
  readonly subject_id: string;
  readonly capability: SemanticComputeCapability;
  readonly scope_kind: "session" | "room";
  readonly scope_id: string;
  readonly direction: SemanticComputeCapabilityDirection | "bidirectional";
  readonly epoch: number;
  readonly expires_at_ms: number;
  readonly revoked: boolean;
}

export interface SemanticComputeCapabilityGrantRequest {
  readonly sessionId: string;
  readonly roomId?: string;
  readonly epoch: number;
  readonly subjectId: string;
  readonly capability: SemanticComputeCapability;
  readonly direction: SemanticComputeCapabilityDirection;
  readonly expiresAtMs: number;
}

const CAPABILITY_HEADER = "X-Semantic-Capability-Grant";
const CONTROL_DATA_TYPE = "application/vnd.ananta.semantic-media-control+json";
const CONTROL_PURPOSE = "semantic_media_control";
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

@Injectable({ providedIn: "root" })
export class SemanticComputeContractApiService {
  private readonly core = inject(HubApiCoreService);

  issueCapabilityGrant(
    hubUrl: string,
    request: SemanticComputeCapabilityGrantRequest,
    idempotencyKey: string,
  ): Observable<SemanticComputeCapabilityGrantReadModel> {
    const base = normalizeHubUrl(hubUrl);
    const scopeKind = request.roomId ? "room" : "session";
    const scopeId = request.roomId
      ? identifier(request.roomId)
      : identifier(request.sessionId);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/capability-grants`,
        base,
        {
          body: {
            session_id: identifier(request.sessionId),
            ...(request.roomId ? { room_id: identifier(request.roomId) } : {}),
            epoch: positiveInteger(request.epoch),
            subject_id: identifier(request.subjectId),
            subject_role: "participant",
            capability: enumValue(request.capability, [
              "publish",
              "subscribe",
              "compute",
              "validate",
            ] as const),
            scope_kind: scopeKind,
            scope_id: scopeId,
            direction: enumValue(request.direction, [
              "ingress",
              "egress",
            ] as const),
            data_type: CONTROL_DATA_TYPE,
            purpose: CONTROL_PURPOSE,
            expires_at_ms: positiveInteger(request.expiresAtMs),
          },
          headers: { "Idempotency-Key": mutationKey(idempotencyKey) },
        },
      )
      .pipe(map((value) => capabilityGrantFromResponse(value)));
  }

  listCapabilityGrants(
    hubUrl: string,
    sessionId: string,
    epoch: number,
    roomId?: string,
  ): Observable<readonly SemanticComputeCapabilityGrantReadModel[]> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      epoch: String(positiveInteger(epoch)),
      scope_kind: roomId ? "room" : "session",
      scope_id: identifier(roomId ?? sessionId),
    });
    return this.core
      .request<unknown>(
        "GET",
        `${base}/v1/semantic-media/capability-grants?${query}`,
        base,
      )
      .pipe(
        map((value) => {
          const response = record(value);
          const items = response["grants"] ?? response["data"];
          if (!Array.isArray(items) || items.length > 200)
            throw new Error("semantic_compute_grants_invalid");
          return Object.freeze(items.map(parseSemanticComputeCapabilityGrant));
        }),
      );
  }

  revokeCapabilityGrant(
    hubUrl: string,
    grantId: string,
  ): Observable<SemanticComputeCapabilityGrantReadModel> {
    const base = normalizeHubUrl(hubUrl);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/capability-grants/${encodeURIComponent(identifier(grantId))}/revoke`,
        base,
      )
      .pipe(map((value) => capabilityGrantFromResponse(value)));
  }

  list(
    hubUrl: string,
    sessionId: string,
    epoch: number,
    grantId: string,
  ): Observable<readonly SemanticComputeContractReadModel[]> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      epoch: String(positiveInteger(epoch)),
    });
    return this.core
      .request<unknown>(
        "GET",
        `${base}/v1/semantic-media/contracts?${query.toString()}`,
        base,
        {
          headers: capabilityHeaders(grantId),
        },
      )
      .pipe(
        map((value) => {
          const response = record(value);
          const container = record(response["contracts"] ?? response["data"]);
          const items = container["items"];
          if (!Array.isArray(items) || items.length > 100)
            throw new Error("semantic_compute_list_invalid");
          return Object.freeze(
            items.map((item) => parseSemanticComputeContract(item)),
          );
        }),
      );
  }

  createOffer(
    hubUrl: string,
    request: SemanticComputeOfferRequest,
    idempotencyKey: string,
    grantId: string,
  ): Observable<SemanticComputeContractReadModel> {
    const base = normalizeHubUrl(hubUrl);
    const body = {
      session_id: identifier(request.sessionId),
      ...(request.roomId ? { room_id: identifier(request.roomId) } : {}),
      epoch: positiveInteger(request.epoch),
      policy_version: identifier(request.policyVersion),
      consent_version: positiveInteger(request.consentVersion),
      proposal: { ...request.proposal },
      advertisements: request.advertisements.map((value) => ({ ...value })),
    };
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/contracts/offers`,
        base,
        {
          body,
          headers: {
            "Idempotency-Key": mutationKey(idempotencyKey),
            ...capabilityHeaders(grantId),
          },
        },
      )
      .pipe(map(contractFromResponse));
  }

  mutate(
    hubUrl: string,
    contractId: string,
    action: SemanticComputeAction,
    request: SemanticComputeMutationRequest,
    idempotencyKey: string,
    grantId: string,
  ): Observable<SemanticComputeContractReadModel> {
    const base = normalizeHubUrl(hubUrl);
    const expectedRevision = positiveInteger(request.expectedRevision);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/contracts/${encodeURIComponent(identifier(contractId))}/${action}`,
        base,
        {
          body: {
            session_id: identifier(request.sessionId),
            epoch: positiveInteger(request.epoch),
            expected_revision: expectedRevision,
            consent_version: nonnegativeInteger(request.consentVersion),
            proposal: { ...(request.proposal ?? {}) },
            advertisements: (request.advertisements ?? []).map((value) => ({
              ...value,
            })),
          },
          headers: {
            "Idempotency-Key": mutationKey(idempotencyKey),
            "If-Match": `"${expectedRevision}"`,
            ...capabilityHeaders(grantId),
          },
        },
      )
      .pipe(map(contractFromResponse));
  }

  registerCandidateKey(
    hubUrl: string,
    request: {
      sessionId: string;
      epoch: number;
      keyId: string;
      publicKeyB64: string;
      expiresAtMs: number;
    },
    grantId: string,
  ): Observable<void> {
    const base = normalizeHubUrl(hubUrl);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/compute/candidate-keys`,
        base,
        {
          body: {
            session_id: identifier(request.sessionId),
            epoch: positiveInteger(request.epoch),
            key_id: identifier(request.keyId),
            public_key_b64: request.publicKeyB64,
            expires_at_ms: positiveInteger(request.expiresAtMs),
          },
          headers: capabilityHeaders(grantId),
        },
      )
      .pipe(map(() => undefined));
  }

  advertiseCapability(
    hubUrl: string,
    advertisement: Readonly<Record<string, unknown>>,
    grantId: string,
  ): Observable<void> {
    const base = normalizeHubUrl(hubUrl);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/compute/capabilities`,
        base,
        {
          body: { ...advertisement },
          headers: capabilityHeaders(grantId),
        },
      )
      .pipe(map(() => undefined));
  }

  candidateClaims(
    hubUrl: string,
    sessionId: string,
    epoch: number,
    grantId: string,
    roomId?: string,
  ): Observable<readonly SemanticComputeCandidateClaim[]> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      epoch: String(positiveInteger(epoch)),
    });
    if (roomId) query.set("room_id", identifier(roomId));
    return this.core
      .request<unknown>(
        "GET",
        `${base}/v1/semantic-media/compute/capabilities?${query}`,
        base,
        {
          headers: capabilityHeaders(grantId),
        },
      )
      .pipe(
        map((value) => {
          const response = record(value);
          const container = record(
            response["capabilities"] ?? response["data"],
          );
          const items = container["items"];
          if (!Array.isArray(items) || items.length > 128)
            throw new Error("semantic_compute_claims_invalid");
          return Object.freeze(items.map(parseCandidateClaim));
        }),
      );
  }

  schedule(
    hubUrl: string,
    contract: SemanticComputeContractReadModel,
    request: {
      sessionId: string;
      epoch: number;
      taskType: string;
      audience: string;
      sequenceStart: number;
      sequenceEnd: number;
      deadlineEpochMs: number;
      resourceBudget: Readonly<{
        cpu_ms: number;
        memory_bytes: number;
        artifact_bytes: number;
      }>;
      validatorCount?: number;
      hotStandby?: boolean;
    },
    idempotencyKey: string,
    grantId: string,
  ): Observable<readonly SemanticComputeLeaseReadModel[]> {
    const base = normalizeHubUrl(hubUrl);
    const revision = positiveInteger(contract.revision);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/contracts/${encodeURIComponent(identifier(contract.contract_id))}/schedule`,
        base,
        {
          body: {
            session_id: identifier(request.sessionId),
            epoch: positiveInteger(request.epoch),
            expected_revision: revision,
            task_type: identifier(request.taskType),
            audience: identifier(request.audience),
            sequence_start: nonnegativeInteger(request.sequenceStart),
            sequence_end: nonnegativeInteger(request.sequenceEnd),
            resource_budget: parseBudget(request.resourceBudget),
            deadline_epoch_ms: positiveInteger(request.deadlineEpochMs),
            validator_count: nonnegativeInteger(request.validatorCount ?? 0),
            hot_standby: request.hotStandby === true,
          },
          headers: {
            "Idempotency-Key": mutationKey(idempotencyKey),
            "If-Match": `"${revision}"`,
            ...capabilityHeaders(grantId),
          },
        },
      )
      .pipe(
        map((value) => {
          const response = record(value);
          const schedule = record(response["schedule"] ?? response["data"]);
          const leases = schedule["leases"];
          if (!Array.isArray(leases) || leases.length > 4)
            throw new Error("semantic_compute_schedule_invalid");
          return Object.freeze(
            leases.map((item) => parseSemanticComputeLease(item)),
          );
        }),
      );
  }

  leases(
    hubUrl: string,
    contractId: string,
    sessionId: string,
    epoch: number,
    grantId: string,
  ): Observable<readonly SemanticComputeLeaseReadModel[]> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      session_id: identifier(sessionId),
      epoch: String(positiveInteger(epoch)),
    });
    return this.core
      .request<unknown>(
        "GET",
        `${base}/v1/semantic-media/contracts/${encodeURIComponent(identifier(contractId))}/leases?${query}`,
        base,
        { headers: capabilityHeaders(grantId) },
      )
      .pipe(
        map((value) => {
          const response = record(value);
          const container = record(response["leases"] ?? response["data"]);
          const items = container["items"];
          if (!Array.isArray(items) || items.length > 200)
            throw new Error("semantic_compute_leases_invalid");
          return Object.freeze(
            items.map((item) => parseSemanticComputeLease(item)),
          );
        }),
      );
  }

  explain(
    hubUrl: string,
    contract: SemanticComputeContractReadModel,
    grantId: string,
  ): Observable<SemanticComputeExplanationReadModel> {
    const base = normalizeHubUrl(hubUrl);
    const query = new URLSearchParams({
      session_id: contract.session_id,
      epoch: String(contract.epoch),
      expected_revision: String(contract.revision),
      expected_digest: contract.digest,
    });
    return this.core
      .request<unknown>(
        "GET",
        `${base}/v1/semantic-media/contracts/${encodeURIComponent(contract.contract_id)}/explanation?${query}`,
        base,
        { headers: capabilityHeaders(grantId) },
      )
      .pipe(
        map((value) =>
          parseExplanation(
            record(value)["explanation"] ?? record(value)["data"],
          ),
        ),
      );
  }

  suggest(
    hubUrl: string,
    contract: SemanticComputeContractReadModel,
    suggestion: Readonly<Record<string, unknown>> | string,
    grantId: string,
  ): Observable<SemanticComputeSuggestionReadModel> {
    const base = normalizeHubUrl(hubUrl);
    return this.core
      .request<unknown>(
        "POST",
        `${base}/v1/semantic-media/contracts/${encodeURIComponent(contract.contract_id)}/suggestions`,
        base,
        {
          body: {
            session_id: contract.session_id,
            epoch: contract.epoch,
            expected_revision: contract.revision,
            expected_digest: contract.digest,
            suggestion,
          },
          headers: capabilityHeaders(grantId),
        },
      )
      .pipe(
        map((value) =>
          parseSuggestion(record(value)["suggestion"] ?? record(value)["data"]),
        ),
      );
  }
}

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

function parseCandidateClaim(value: unknown): SemanticComputeCandidateClaim {
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

function parseExplanation(value: unknown): SemanticComputeExplanationReadModel {
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

function parseSuggestion(value: unknown): SemanticComputeSuggestionReadModel {
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

function contractFromResponse(
  value: unknown,
): SemanticComputeContractReadModel {
  const response = record(value);
  return parseSemanticComputeContract(response["contract"] ?? response["data"]);
}

function capabilityGrantFromResponse(
  value: unknown,
): SemanticComputeCapabilityGrantReadModel {
  const response = record(value);
  return parseSemanticComputeCapabilityGrant(
    response["grant"] ?? response["data"],
  );
}

function capabilityHeaders(grantId: string): Readonly<Record<string, string>> {
  return { [CAPABILITY_HEADER]: identifier(grantId) };
}

function normalizeHubUrl(value: string): string {
  const normalized = String(value || "")
    .trim()
    .replace(/\/+$/, "");
  if (!/^https?:\/\/[^\s]+$/.test(normalized))
    throw new Error("semantic_compute_hub_url_invalid");
  return normalized;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("semantic_compute_response_invalid");
  return value as Record<string, unknown>;
}

function exactRecord(
  value: unknown,
  fields: readonly string[],
  reason: string,
): Record<string, unknown> {
  const row = record(value);
  if (
    Object.keys(row).length !== fields.length ||
    fields.some((field) => !Object.hasOwn(row, field))
  ) {
    throw new Error(reason);
  }
  return row;
}

function exactRecordWithOptional(
  value: unknown,
  required: readonly string[],
  optional: readonly string[],
  reason: string,
): Record<string, unknown> {
  const row = record(value);
  const allowed = new Set([...required, ...optional]);
  if (
    Object.keys(row).some((field) => !allowed.has(field)) ||
    required.some((field) => !Object.hasOwn(row, field))
  ) {
    throw new Error(reason);
  }
  return row;
}

function identifier(value: unknown): string {
  const rendered = String(value ?? "");
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(rendered))
    throw new Error("semantic_compute_identifier_invalid");
  return rendered;
}

function executorIdentifier(value: unknown): string {
  if (typeof value !== "string")
    throw new Error("semantic_compute_executor_id_invalid");
  if (/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value)) return value;
  if (
    value !== value.trim() ||
    new TextEncoder().encode(value).byteLength > 512 ||
    /\s/.test(value)
  ) {
    throw new Error("semantic_compute_executor_id_invalid");
  }
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== ""
    )
      throw new Error("unsafe");
  } catch {
    throw new Error("semantic_compute_executor_id_invalid");
  }
  return value;
}

function digest(value: unknown): string {
  const rendered = String(value ?? "");
  if (!/^[a-f0-9]{64}$/.test(rendered))
    throw new Error("semantic_compute_digest_invalid");
  return rendered;
}

function boundedText(
  value: unknown,
  minimum: number,
  maximumBytes: number,
): string {
  if (typeof value !== "string")
    throw new Error("semantic_compute_text_invalid");
  const bytes = new TextEncoder().encode(value).byteLength;
  if (bytes < minimum || bytes > maximumBytes)
    throw new Error("semantic_compute_text_invalid");
  return value;
}

function boundedInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    Number(value) < minimum ||
    Number(value) > maximum
  ) {
    throw new Error("semantic_compute_integer_invalid");
  }
  return Number(value);
}

function finiteNumber(
  value: unknown,
  minimum: number,
  maximum: number,
): number {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw new Error("semantic_compute_number_invalid");
  }
  return value;
}

function positiveInteger(value: unknown): number {
  return boundedInteger(value, 1, Number.MAX_SAFE_INTEGER);
}
function nonnegativeInteger(value: unknown): number {
  return boundedInteger(value, 0, Number.MAX_SAFE_INTEGER);
}

function stringArray(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum)
    throw new Error("semantic_compute_array_invalid");
  return value.map(identifier);
}

function parseBudget(value: unknown): {
  cpu_ms: number;
  memory_bytes: number;
  artifact_bytes: number;
} {
  const budget = record(value);
  if (
    Object.keys(budget).sort().join(",") !==
    "artifact_bytes,cpu_ms,memory_bytes"
  ) {
    throw new Error("semantic_compute_budget_invalid");
  }
  return {
    cpu_ms: boundedInteger(budget["cpu_ms"], 1, 60_000),
    memory_bytes: boundedInteger(budget["memory_bytes"], 1, 4_294_967_296),
    artifact_bytes: boundedInteger(budget["artifact_bytes"], 1, 4_194_304),
  };
}

function enumValue<const T extends readonly string[]>(
  value: unknown,
  values: T,
): T[number] {
  if (!values.includes(value as T[number]))
    throw new Error("semantic_compute_enum_invalid");
  return value as T[number];
}

function mutationKey(value: string): string {
  const rendered = String(value || "").trim();
  if (rendered.length < 8 || rendered.length > 256 || /\s/.test(rendered)) {
    throw new Error("semantic_compute_idempotency_key_invalid");
  }
  return rendered;
}
