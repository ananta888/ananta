export type CollaborationContractKind =
  | 'workspace' | 'actor' | 'room' | 'event' | 'membership'
  | 'live' | 'resource' | 'intent' | 'bridge_capability';

export interface CollaborationContractContext {
  expected_tenant_id?: string;
  expected_workspace_id?: string;
  expected_revision?: number;
}

export class CollaborationContractGateError extends Error {
  constructor(readonly reasonCode: string) {
    super(reasonCode);
    this.name = 'CollaborationContractGateError';
  }
}

type JsonRecord = Record<string, unknown>;
type Digest = (value: unknown) => Promise<string>;

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const EVENT_TYPES = new Set([
  'message.posted', 'message.replied', 'thread.resolved', 'thread.reopened', 'thread.tombstoned',
  'decision.recorded', 'review.recorded', 'artifact.linked', 'task.projected', 'workflow.projected',
  'git.projected', 'command.proposed', 'command.decided', 'membership.changed', 'room.changed',
  'legacy.share_session.observed', 'event.redacted',
]);

export async function validateCollaborationContract(
  kind: CollaborationContractKind,
  payload: unknown,
  context: CollaborationContractContext = {},
  digest: Digest = browserDigest,
): Promise<JsonRecord> {
  const value = record(payload, `collaboration_${kind}_invalid`);
  const validators: Record<CollaborationContractKind, (item: JsonRecord, hash: Digest) => Promise<void>> = {
    workspace: validateWorkspace,
    actor: validateActor,
    room: validateRoom,
    event: validateEvent,
    membership: validateMembership,
    live: validateLive,
    resource: validateResource,
    intent: validateIntent,
    bridge_capability: validateBridgeCapability,
  };
  await validators[kind](value, digest);
  if (context.expected_tenant_id !== undefined && value['tenant_id'] !== context.expected_tenant_id) {
    fail('collaboration_tenant_scope_mismatch');
  }
  if (context.expected_workspace_id !== undefined && value['workspace_id'] !== context.expected_workspace_id) {
    fail('collaboration_workspace_scope_mismatch');
  }
  if (context.expected_revision !== undefined && value['revision'] !== context.expected_revision) {
    fail('collaboration_revision_stale');
  }
  return structuredClone(value);
}

async function validateWorkspace(value: JsonRecord): Promise<void> {
  exact(value, [
    'schema', 'tenant_id', 'workspace_id', 'project_id', 'title', 'state', 'retention', 'revision',
    'created_by', 'created_at', 'native_core', 'bridge_required', 'human_intervention_required',
  ], 'collaboration_workspace_fields_invalid');
  if (
    value['schema'] !== 'ananta.collaboration-workspace.v1' || !id(value['tenant_id']) ||
    !id(value['workspace_id']) || (value['project_id'] !== null && !id(value['project_id'])) ||
    !text(value['title'], 200) || value['state'] !== 'active' ||
    !['standard', 'audit', 'legal_hold'].includes(String(value['retention'])) ||
    !integer(value['revision'], 1) || !id(value['created_by']) || !finite(value['created_at']) ||
    value['native_core'] !== true || value['bridge_required'] !== false ||
    value['human_intervention_required'] !== false
  ) fail('collaboration_workspace_invalid');
}

async function validateActor(value: JsonRecord): Promise<void> {
  const required = ['schema', 'actor_binding_id', 'actor_kind', 'authority_kind', 'authority_subject', 'display_name', 'capabilities'];
  optionalExact(value, required, ['profile'], 'collaboration_actor_binding_fields_invalid');
  if (
    value['schema'] !== 'ananta.collaboration-actor-binding.v1' || !id(value['actor_binding_id']) ||
    !['human', 'agent', 'worker', 'resource', 'service', 'external_actor'].includes(String(value['actor_kind'])) ||
    !['oidc', 'hub_agent', 'registered_worker', 'resource_registry', 'service', 'bridge'].includes(String(value['authority_kind'])) ||
    !text(value['authority_subject'], 256) || !text(value['display_name'], 128) ||
    !idList(value['capabilities'], 32, true)
  ) fail('collaboration_actor_binding_invalid');
  if (value['profile'] !== undefined) {
    const profile = record(value['profile'], 'collaboration_actor_profile_invalid');
    exact(profile, ['provider', 'model', 'profile_revision'], 'collaboration_actor_profile_invalid');
    if (!text(profile['provider'], 128) || !text(profile['model'], 256) || !id(profile['profile_revision'])) {
      fail('collaboration_actor_profile_invalid');
    }
  }
}

async function validateRoom(value: JsonRecord): Promise<void> {
  exact(value, ['schema', 'room_id', 'room_kind', 'title', 'binding_kind', 'binding_id'], 'collaboration_room_fields_invalid');
  const bound = value['binding_kind'] !== null || value['binding_id'] !== null;
  if (
    value['schema'] !== 'ananta.collaboration-room.v1' || !id(value['room_id']) ||
    !['project', 'goal', 'task', 'branch', 'incident', 'pair_session', 'freeform'].includes(String(value['room_kind'])) ||
    !text(value['title'], 200) || ((value['binding_kind'] === null) !== (value['binding_id'] === null)) ||
    (bound && (!id(value['binding_kind']) || !id(value['binding_id'])))
  ) fail('collaboration_room_invalid');
}

async function validateEvent(value: JsonRecord, digest: Digest): Promise<void> {
  exact(value, [
    'schema', 'event_id', 'workspace_id', 'room_id', 'thread_id', 'event_type', 'actor_binding_id',
    'idempotency_key', 'correlation_id', 'causation_id', 'visibility', 'retention', 'occurred_at',
    'payload', 'payload_digest', 'source_refs', 'run_refs',
  ], 'collaboration_event_fields_invalid');
  const payload = object(value['payload']);
  if (
    value['schema'] !== 'ananta.workspace-event.v1' || !id(value['event_id']) || !id(value['workspace_id']) ||
    !nullableId(value['room_id']) || !nullableId(value['thread_id']) ||
    !EVENT_TYPES.has(String(value['event_type'])) || !id(value['actor_binding_id']) ||
    !id(value['idempotency_key']) || !id(value['correlation_id']) || !nullableId(value['causation_id']) ||
    !['workspace', 'room', 'restricted'].includes(String(value['visibility'])) ||
    !['ephemeral', 'standard', 'audit', 'legal_hold'].includes(String(value['retention'])) ||
    !finite(value['occurred_at']) || !payload || bytes(canonicalJson(payload)) > 65_536 ||
    !digestValue(value['payload_digest']) || !idList(value['source_refs'], 64, true, 'SRC_') ||
    !idList(value['run_refs'], 64, true, 'RUN_')
  ) fail('collaboration_event_invalid');
  if (await digest(payload) !== value['payload_digest']) fail('collaboration_payload_digest_mismatch');
  if (
    ['decision.recorded', 'review.recorded', 'task.projected', 'workflow.projected', 'git.projected'].includes(String(value['event_type'])) &&
    ((value['source_refs'] as unknown[]).length === 0 || (value['run_refs'] as unknown[]).length === 0)
  ) fail('collaboration_grounded_evidence_required');
}

async function validateMembership(value: JsonRecord): Promise<void> {
  exact(value, ['schema', 'workspace_id', 'actor_binding_id', 'role', 'status', 'revision', 'capabilities'], 'collaboration_membership_fields_invalid');
  if (
    value['schema'] !== 'ananta.collaboration-membership.v1' || !id(value['workspace_id']) ||
    !id(value['actor_binding_id']) ||
    !['owner', 'maintainer', 'member', 'guest', 'observer', 'editor', 'viewer'].includes(String(value['role'])) ||
    !['active', 'revoked'].includes(String(value['status'])) || !integer(value['revision'], 1) ||
    !idList(value['capabilities'], 32, true)
  ) fail('collaboration_membership_invalid');
}

async function validateLive(value: JsonRecord, digest: Digest): Promise<void> {
  exact(value, [
    'schema', 'envelope_id', 'workspace_id', 'room_id', 'publisher_actor_binding_id', 'traffic_class',
    'publisher_epoch', 'created_at', 'payload', 'payload_digest', 'durable_event_id',
  ], 'collaboration_live_envelope_fields_invalid');
  const payload = object(value['payload']);
  if (
    value['schema'] !== 'ananta.collaboration-live-envelope.v1' || !id(value['envelope_id']) ||
    !id(value['workspace_id']) || !id(value['room_id']) || !id(value['publisher_actor_binding_id']) ||
    !['revocation', 'control', 'durable_projection', 'semantic', 'presence', 'bulk_reference'].includes(String(value['traffic_class'])) ||
    !integer(value['publisher_epoch'], 1) || !finite(value['created_at']) || !payload ||
    bytes(canonicalJson(payload)) > 65_536 || 'audience' in payload || 'receivers' in payload ||
    !digestValue(value['payload_digest']) || !nullableId(value['durable_event_id'])
  ) fail('collaboration_live_envelope_invalid');
  if (await digest(payload) !== value['payload_digest']) fail('collaboration_live_payload_digest_mismatch');
  if (value['traffic_class'] === 'durable_projection' && value['durable_event_id'] === null) {
    fail('collaboration_live_durable_identity_required');
  }
}

async function validateResource(value: JsonRecord): Promise<void> {
  exact(value, [
    'schema', 'offer_id', 'workspace_id', 'owner_actor_binding_id', 'resource_id', 'capability_category',
    'capacity_class', 'scopes', 'expires_at', 'sensitivity', 'attestation_status', 'metadata',
  ], 'collaboration_resource_offer_fields_invalid');
  const metadata = object(value['metadata']);
  if (
    value['schema'] !== 'ananta.collaboration-resource-offer.v1' || !id(value['offer_id']) ||
    !id(value['workspace_id']) || !id(value['owner_actor_binding_id']) || !id(value['resource_id']) ||
    !['compute', 'model', 'repository', 'terminal', 'tool'].includes(String(value['capability_category'])) ||
    !['small', 'medium', 'large'].includes(String(value['capacity_class'])) || !idList(value['scopes'], 32, false) ||
    !finite(value['expires_at']) || !['workspace', 'restricted'].includes(String(value['sensitivity'])) ||
    !['verified', 'unverified', 'test_only'].includes(String(value['attestation_status'])) || !metadata
  ) fail('collaboration_resource_offer_invalid');
  if (Object.keys(metadata).some(key => ['endpoint', 'private_endpoint', 'raw_telemetry', 'secret', 'token', 'local_path'].includes(key.toLowerCase()))) {
    fail('collaboration_resource_offer_sensitive_metadata');
  }
  if (bytes(canonicalJson(metadata)) > 4_096) fail('collaboration_resource_offer_metadata_too_large');
}

async function validateIntent(value: JsonRecord, digest: Digest): Promise<void> {
  const required = [
    'schema', 'intent_id', 'workspace_id', 'room_id', 'actor_binding_id', 'intent_type',
    'target_actor_binding_id', 'task_id', 'correlation_id', 'causation_id', 'hop_count', 'payload', 'payload_digest',
  ];
  optionalExact(value, required, ['origin_event_type'], 'collaboration_agent_intent_fields_invalid');
  const payload = object(value['payload']);
  if (
    value['schema'] !== 'ananta.collaboration-agent-intent.v1' || !id(value['intent_id']) ||
    !id(value['workspace_id']) || !id(value['room_id']) || !id(value['actor_binding_id']) ||
    !['mention', 'answer', 'propose_task', 'request_context', 'handoff_request'].includes(String(value['intent_type'])) ||
    !nullableId(value['target_actor_binding_id']) || !nullableId(value['task_id']) || !id(value['correlation_id']) ||
    !nullableId(value['causation_id']) || !integer(value['hop_count'], 0, 8) || !payload ||
    !digestValue(value['payload_digest'])
  ) fail('collaboration_agent_intent_invalid');
  if (containsForbidden(payload, new Set(['assignment_id', 'budget', 'provider', 'team_id', 'tools', 'worker_id']))) {
    fail('collaboration_agent_intent_authority_escalation');
  }
  if (
    ['workflow.projected', 'task.projected'].includes(String(value['origin_event_type'])) &&
    ['propose_task', 'handoff_request'].includes(String(value['intent_type']))
  ) fail('collaboration_agent_intent_workflow_retrigger_forbidden');
  if (await digest(payload) !== value['payload_digest']) fail('collaboration_agent_intent_digest_mismatch');
}

async function validateBridgeCapability(value: JsonRecord): Promise<void> {
  exact(value, [
    'schema', 'state', 'mapping_versions', 'supports_outbound', 'supports_inbound_proposals',
    'supports_command_intents', 'native_core_available',
  ], 'collaboration_bridge_capability_fields_invalid');
  if (
    value['schema'] !== 'ananta.collaboration-bridge-capability.v1' ||
    !['disabled', 'disconnected', 'connected'].includes(String(value['state'])) ||
    !idList(value['mapping_versions'], 16, true) ||
    !['supports_outbound', 'supports_inbound_proposals', 'supports_command_intents'].every(key => typeof value[key] === 'boolean') ||
    value['native_core_available'] !== true
  ) fail('collaboration_bridge_capability_invalid');
}

function record(value: unknown, reason: string): JsonRecord {
  const result = object(value);
  if (!result) fail(reason);
  return result;
}

function object(value: unknown): JsonRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : null;
}

function exact(value: JsonRecord, fields: string[], reason: string): void {
  if (Object.keys(value).sort().join('|') !== [...fields].sort().join('|')) fail(reason);
}

function optionalExact(value: JsonRecord, required: string[], optional: string[], reason: string): void {
  const keys = Object.keys(value);
  if (required.some(key => !(key in value)) || keys.some(key => !required.includes(key) && !optional.includes(key))) fail(reason);
}

function id(value: unknown): value is string { return typeof value === 'string' && ID.test(value.trim()); }
function nullableId(value: unknown): boolean { return value === null || id(value); }
function digestValue(value: unknown): value is string { return typeof value === 'string' && DIGEST.test(value); }
function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= maximum;
}
function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.trim().length >= 1 && value.trim().length <= maximum &&
    ![...value.trim()].some(character => character.codePointAt(0)! < 32);
}
function idList(value: unknown, maximum: number, emptyAllowed: boolean, prefix?: string): value is string[] {
  return Array.isArray(value) && (emptyAllowed || value.length > 0) && value.length <= maximum &&
    value.every(item => id(item) && (!prefix || item.startsWith(prefix))) && new Set(value).size === value.length;
}
function containsForbidden(value: unknown, forbidden: Set<string>): boolean {
  if (Array.isArray(value)) return value.some(item => containsForbidden(item, forbidden));
  const item = object(value);
  return item ? Object.entries(item).some(([key, nested]) => forbidden.has(key.toLowerCase()) || containsForbidden(nested, forbidden)) : false;
}
function bytes(value: string): number { return new TextEncoder().encode(value).byteLength; }
function fail(reason: string): never { throw new CollaborationContractGateError(reason); }

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  const item = object(value);
  if (item) return `{${Object.keys(item).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(item[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

async function browserDigest(value: unknown): Promise<string> {
  const bytesValue = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest('SHA-256', bytesValue);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
