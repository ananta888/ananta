import { canonicalSecurityJson } from './webrtc-secure-envelope';

export interface StrictSecurityProposalV1 {
  version: 1;
  negotiation_id: string;
  scope_kind: 'session';
  scope_id: string;
  sender_id: string;
  recipient_id: string;
  minimum_mode: 'strict_e2ee';
  selected_mode: 'strict_e2ee';
  algorithms: ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'];
  key_epoch: number;
  payload_classes: ['bulk', 'control', 'semantic'];
  expires_at_ms: number;
}

export interface FinalStrictPairSecurityContractV1 {
  version: 1;
  negotiation_id: string;
  offer: StrictSecurityProposalV1;
  answer: StrictSecurityProposalV1;
  digest: string;
  signature: string;
  signature_algorithm: 'HMAC-SHA256';
}

export class SecurityNegotiationError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const PROPOSAL_FIELDS = [
  'version', 'negotiation_id', 'scope_kind', 'scope_id', 'sender_id', 'recipient_id',
  'minimum_mode', 'selected_mode', 'algorithms', 'key_epoch', 'payload_classes',
  'expires_at_ms',
] as const;
const CONTRACT_FIELDS = [
  'version', 'negotiation_id', 'offer', 'answer', 'digest', 'signature',
  'signature_algorithm',
] as const;

/**
 * Validate the control-plane-finalized bilateral transcript and independently recompute
 * its digest before that digest is accepted by the signed peer-key package.
 */
export async function validateFinalStrictPairSecurityContract(
  raw: unknown,
  options: {
    scopeId: string;
    epoch: number;
    remoteMembershipId: string;
    localMembershipId?: string;
    nowMs?: number;
  },
): Promise<FinalStrictPairSecurityContractV1> {
  const value = closedObject(raw, CONTRACT_FIELDS, 'security_contract_fields_invalid');
  if (value['version'] !== 1 || value['signature_algorithm'] !== 'HMAC-SHA256') {
    throw new SecurityNegotiationError('security_contract_version_invalid');
  }
  if (!identifier(value['negotiation_id']) || !DIGEST_RE.test(String(value['digest'] ?? ''))
    || !DIGEST_RE.test(String(value['signature'] ?? ''))) {
    throw new SecurityNegotiationError('security_contract_authentication_invalid');
  }
  const offer = parseStrictProposal(value['offer'], options);
  const answer = parseStrictProposal(value['answer'], options);
  const membershipIds = new Set([offer.sender_id, offer.recipient_id]);
  if (
    offer.negotiation_id !== value['negotiation_id']
    || answer.negotiation_id !== value['negotiation_id']
    || offer.sender_id !== answer.recipient_id
    || offer.recipient_id !== answer.sender_id
    || offer.sender_id === offer.recipient_id
    || !membershipIds.has(options.remoteMembershipId)
    || (options.localMembershipId !== undefined && (
      options.localMembershipId === options.remoteMembershipId
      || membershipIds.size !== 2
      || !membershipIds.has(options.localMembershipId)
    ))
  ) {
    throw new SecurityNegotiationError('negotiation_binding_mismatch');
  }
  const digest = await sha256Hex(canonicalSecurityJson({
    domain: 'ananta.webrtc.security-negotiation.v1',
    offer,
    answer,
  }));
  if (digest !== value['digest']) throw new SecurityNegotiationError('security_contract_digest_mismatch');
  return {
    version: 1,
    negotiation_id: value['negotiation_id'] as string,
    offer,
    answer,
    digest,
    signature: value['signature'] as string,
    signature_algorithm: 'HMAC-SHA256',
  };
}

function parseStrictProposal(
  raw: unknown,
  options: { scopeId: string; epoch: number; nowMs?: number },
): StrictSecurityProposalV1 {
  const value = closedObject(raw, PROPOSAL_FIELDS, 'negotiation_fields_invalid');
  for (const field of ['negotiation_id', 'scope_id', 'sender_id', 'recipient_id'] as const) {
    if (!identifier(value[field])) throw new SecurityNegotiationError('negotiation_identity_invalid');
  }
  if (
    value['version'] !== 1
    || value['scope_kind'] !== 'session'
    || value['scope_id'] !== options.scopeId
    || value['minimum_mode'] !== 'strict_e2ee'
    || value['selected_mode'] !== 'strict_e2ee'
  ) throw new SecurityNegotiationError('security_downgrade_rejected');
  if (!Number.isSafeInteger(value['key_epoch']) || value['key_epoch'] !== options.epoch) {
    throw new SecurityNegotiationError('epoch_mismatch');
  }
  if (!Number.isSafeInteger(value['expires_at_ms']) || (value['expires_at_ms'] as number) <= (options.nowMs ?? Date.now())) {
    throw new SecurityNegotiationError('negotiation_expired');
  }
  if (!exactStringArray(value['algorithms'], ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'])) {
    throw new SecurityNegotiationError('algorithm_invalid');
  }
  if (!exactStringArray(value['payload_classes'], ['bulk', 'control', 'semantic'])) {
    throw new SecurityNegotiationError('payload_class_invalid');
  }
  return value as unknown as StrictSecurityProposalV1;
}

function closedObject(
  raw: unknown,
  expected: readonly string[],
  reasonCode: string,
): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new SecurityNegotiationError(reasonCode);
  }
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== expected.length || expected.some((field) => !(field in value))) {
    throw new SecurityNegotiationError(reasonCode);
  }
  return value;
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && ID_RE.test(value);
}

function exactStringArray(value: unknown, expected: readonly string[]): boolean {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}
