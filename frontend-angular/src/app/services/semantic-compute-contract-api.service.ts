import { Injectable, inject } from "@angular/core";
import { Observable, map } from "rxjs";

import { HubApiCoreService } from "./hub-api-core.service";
import type {
  SemanticComputeAction,
  SemanticComputeCandidateClaim,
  SemanticComputeCapabilityGrantReadModel,
  SemanticComputeCapabilityGrantRequest,
  SemanticComputeContractReadModel,
  SemanticComputeExplanationReadModel,
  SemanticComputeLeaseReadModel,
  SemanticComputeMutationRequest,
  SemanticComputeOfferRequest,
  SemanticComputeSuggestionReadModel,
} from "./semantic-compute-contract.models";
import {
  capabilityGrantFromResponse,
  contractFromResponse,
  parseCandidateClaim,
  parseExplanation,
  parseSemanticComputeCapabilityGrant,
  parseSemanticComputeContract,
  parseSemanticComputeLease,
  parseSuggestion,
} from "./semantic-compute-contract.parsers";
import {
  digest,
  enumValue,
  identifier,
  mutationKey,
  nonnegativeInteger,
  parseBudget,
  positiveInteger,
  record,
} from "./semantic-compute-contract.values";

export * from "./semantic-compute-contract.models";
export {
  parseSemanticComputeCapabilityGrant,
  parseSemanticComputeContract,
  parseSemanticComputeLease,
  parseSemanticTaskLease,
} from "./semantic-compute-contract.parsers";

const CAPABILITY_HEADER = "X-Semantic-Capability-Grant";
const CONTROL_DATA_TYPE = "application/vnd.ananta.semantic-media-control+json";
const CONTROL_PURPOSE = "semantic_media_control";

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
