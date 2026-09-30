/** Read models, requests and enums of the Hub semantic compute contract API. */

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
