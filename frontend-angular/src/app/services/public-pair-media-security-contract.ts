import type { FinalStrictPairSecurityContractV1 } from './webrtc-security-negotiation';
import type { SignedPeerKeyPackage } from './webrtc-peer-key.service';
import { canonicalSecurityJson, decodeB64 } from './webrtc-secure-envelope';
import { PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 } from './pair-media-frame-format';

export const PUBLIC_PAIR_MEDIA_GRANTS = Object.freeze([
  'microphone-opus',
  'camera-vp8',
  'screen-vp8',
] as const);

export const PUBLIC_PAIR_MEDIA_SLOTS = Object.freeze([
  Object.freeze({ slot: 'microphone-opus', kind: 'audio', codec: 'opus' }),
  Object.freeze({ slot: 'camera-vp8', kind: 'video', codec: 'vp8' }),
  Object.freeze({ slot: 'screen-vp8', kind: 'video', codec: 'vp8' }),
] as const);

export type PublicPairMediaGrant = typeof PUBLIC_PAIR_MEDIA_GRANTS[number];
export type PublicPairMediaSlot = typeof PUBLIC_PAIR_MEDIA_SLOTS[number]['slot'];
export type PublicPairMediaKind = typeof PUBLIC_PAIR_MEDIA_SLOTS[number]['kind'];
export type PublicPairMediaCodec = typeof PUBLIC_PAIR_MEDIA_SLOTS[number]['codec'];

export const PUBLIC_PAIR_MEDIA_CAPABILITIES_V2 = Object.freeze({
  version: 2 as const,
  transform: 'RTCRtpScriptTransform' as const,
  frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
  grants: PUBLIC_PAIR_MEDIA_GRANTS,
});

export interface PublicPairMediaMembershipV2 {
  readonly membership_id: string;
  readonly membership_version: number;
  readonly peer_id: string;
  readonly device_key_fingerprint: string;
  readonly public_media_e2ee_version: 2;
}

export interface PublicPairMediaSecurityContractV2 {
  readonly domain: 'ananta.public-pair.media-security-contract.v2';
  readonly version: 2;
  readonly session_id: string;
  readonly epoch: number;
  readonly identity_binding_version: 2;
  readonly base_security_contract_digest: string;
  readonly memberships: readonly [PublicPairMediaMembershipV2, PublicPairMediaMembershipV2];
  readonly grants: typeof PUBLIC_PAIR_MEDIA_GRANTS;
  readonly slots: typeof PUBLIC_PAIR_MEDIA_SLOTS;
  readonly transform: 'RTCRtpScriptTransform';
  readonly frame_format: typeof PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2;
  readonly algorithms: Readonly<{ aead: 'AES-256-GCM'; kdf: 'HKDF-SHA-256' }>;
  readonly expires_at_ms: number;
  readonly authority_key_id: string;
  readonly digest: string;
  readonly signature_algorithm: 'Ed25519';
  readonly signature_b64: string;
}

export interface PublicPairMediaContractValidationOptions {
  readonly sessionId: string;
  readonly epoch: number;
  readonly localPeerId: string;
  readonly localMembershipId: string;
  readonly localDeviceFingerprint: string;
  readonly remotePackage: SignedPeerKeyPackage;
  readonly baseContract: FinalStrictPairSecurityContractV1;
  readonly authorityKeyId: string;
  readonly authorityPublicKeyB64: string;
  readonly nowMs?: number;
}

export class PublicPairMediaContractError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

const CONTRACT_FIELDS = [
  'domain', 'version', 'session_id', 'epoch', 'identity_binding_version',
  'base_security_contract_digest', 'memberships', 'grants', 'slots', 'transform',
  'frame_format', 'algorithms', 'expires_at_ms', 'authority_key_id', 'digest',
  'signature_algorithm', 'signature_b64',
] as const;
const MEMBERSHIP_FIELDS = [
  'membership_id', 'membership_version', 'peer_id', 'device_key_fingerprint',
  'public_media_e2ee_version',
] as const;
const SLOT_FIELDS = ['slot', 'kind', 'codec'] as const;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const DEVICE_PEER_RE = /^peer:[a-f0-9]{64}$/;

/**
 * Validate the separately signed Public Pair media-v2 grant. The base Pair v1
 * contract deliberately remains data-only; this authority-signed object is
 * the sole additive authorization for encoded audio/video frames.
 */
export async function validatePublicPairMediaSecurityContract(
  raw: unknown,
  options: PublicPairMediaContractValidationOptions,
): Promise<PublicPairMediaSecurityContractV2> {
  const value = closedObject(raw, CONTRACT_FIELDS, 'public_media_contract_fields_invalid');
  if (
    value['domain'] !== 'ananta.public-pair.media-security-contract.v2'
    || value['version'] !== 2
    || value['identity_binding_version'] !== 2
    || value['transform'] !== 'RTCRtpScriptTransform'
    || value['frame_format'] !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
    || value['signature_algorithm'] !== 'Ed25519'
  ) throw new PublicPairMediaContractError('public_media_contract_version_invalid');
  if (
    value['session_id'] !== options.sessionId
    || value['epoch'] !== options.epoch
    || value['base_security_contract_digest'] !== options.baseContract.digest
    || value['authority_key_id'] !== options.authorityKeyId
  ) throw new PublicPairMediaContractError('public_media_contract_binding_mismatch');
  if (!Number.isSafeInteger(value['epoch']) || (value['epoch'] as number) < 1) {
    throw new PublicPairMediaContractError('public_media_contract_epoch_invalid');
  }
  const expiresAtMs = value['expires_at_ms'];
  const baseExpiry = Math.min(
    options.baseContract.offer.expires_at_ms,
    options.baseContract.answer.expires_at_ms,
  );
  if (
    !Number.isSafeInteger(expiresAtMs)
    || (expiresAtMs as number) <= (options.nowMs ?? Date.now())
    || (expiresAtMs as number) > baseExpiry
  ) throw new PublicPairMediaContractError('public_media_contract_expired');
  if (!exactStrings(value['grants'], PUBLIC_PAIR_MEDIA_GRANTS)) {
    throw new PublicPairMediaContractError('public_media_contract_grants_invalid');
  }
  validateSlots(value['slots']);
  const algorithms = closedObject(
    value['algorithms'], ['aead', 'kdf'], 'public_media_contract_algorithms_invalid',
  );
  if (algorithms['aead'] !== 'AES-256-GCM' || algorithms['kdf'] !== 'HKDF-SHA-256') {
    throw new PublicPairMediaContractError('public_media_contract_algorithms_invalid');
  }
  const memberships = validateMemberships(value['memberships'], options);
  if (!DIGEST_RE.test(String(value['digest'] ?? ''))) {
    throw new PublicPairMediaContractError('public_media_contract_digest_invalid');
  }
  const unsigned = { ...value };
  delete unsigned['digest'];
  delete unsigned['signature_algorithm'];
  delete unsigned['signature_b64'];
  const expectedDigest = await sha256Hex(canonicalSecurityJson(unsigned));
  if (expectedDigest !== value['digest']) {
    throw new PublicPairMediaContractError('public_media_contract_digest_mismatch');
  }
  await verifyAuthoritySignature(value, options);
  return Object.freeze({
    ...(value as unknown as PublicPairMediaSecurityContractV2),
    memberships,
    grants: PUBLIC_PAIR_MEDIA_GRANTS,
    slots: PUBLIC_PAIR_MEDIA_SLOTS,
    algorithms: Object.freeze({ aead: 'AES-256-GCM', kdf: 'HKDF-SHA-256' }),
  });
}

function validateMemberships(
  raw: unknown,
  options: PublicPairMediaContractValidationOptions,
): readonly [PublicPairMediaMembershipV2, PublicPairMediaMembershipV2] {
  if (!Array.isArray(raw) || raw.length !== 2) {
    throw new PublicPairMediaContractError('public_media_contract_memberships_invalid');
  }
  const parsed = raw.map(item => {
    const value = closedObject(
      item, MEMBERSHIP_FIELDS, 'public_media_contract_memberships_invalid',
    );
    if (
      !identifier(value['membership_id'])
      || !DEVICE_PEER_RE.test(String(value['peer_id'] ?? ''))
      || !DIGEST_RE.test(String(value['device_key_fingerprint'] ?? ''))
      || !Number.isSafeInteger(value['membership_version'])
      || (value['membership_version'] as number) < 1
      || value['public_media_e2ee_version'] !== 2
    ) throw new PublicPairMediaContractError('public_media_contract_memberships_invalid');
    return Object.freeze(value as unknown as PublicPairMediaMembershipV2);
  }) as [PublicPairMediaMembershipV2, PublicPairMediaMembershipV2];
  const expectedMembershipOrder = [
    options.baseContract.offer.sender_id,
    options.baseContract.offer.recipient_id,
  ];
  if (
    parsed.some((member, index) => member.membership_id !== expectedMembershipOrder[index])
    || parsed[0].membership_id === parsed[1].membership_id
    || parsed[0].peer_id === parsed[1].peer_id
    || parsed[0].device_key_fingerprint === parsed[1].device_key_fingerprint
  ) throw new PublicPairMediaContractError('public_media_contract_memberships_invalid');
  const local = parsed.find(member => member.membership_id === options.localMembershipId);
  const remote = parsed.find(member => member.membership_id === options.remotePackage.membership_id);
  if (
    !local || !remote || local === remote
    || local.peer_id !== options.localPeerId
    || local.device_key_fingerprint !== options.localDeviceFingerprint
    || remote.peer_id !== options.remotePackage.peer_id
    || remote.membership_version !== options.remotePackage.membership_version
    || remote.device_key_fingerprint !== options.remotePackage.device_key_fingerprint
  ) throw new PublicPairMediaContractError('public_media_contract_membership_binding_mismatch');
  return Object.freeze(parsed);
}

function validateSlots(raw: unknown): void {
  if (!Array.isArray(raw) || raw.length !== PUBLIC_PAIR_MEDIA_SLOTS.length) {
    throw new PublicPairMediaContractError('public_media_contract_slots_invalid');
  }
  raw.forEach((item, index) => {
    const value = closedObject(item, SLOT_FIELDS, 'public_media_contract_slots_invalid');
    const expected = PUBLIC_PAIR_MEDIA_SLOTS[index];
    if (
      value['slot'] !== expected.slot
      || value['kind'] !== expected.kind
      || value['codec'] !== expected.codec
    ) throw new PublicPairMediaContractError('public_media_contract_slots_invalid');
  });
}

async function verifyAuthoritySignature(
  value: Record<string, unknown>,
  options: PublicPairMediaContractValidationOptions,
): Promise<void> {
  try {
    const publicKeyBytes = decodeB64(options.authorityPublicKeyB64);
    const signatureBytes = decodeB64(value['signature_b64']);
    if (publicKeyBytes.byteLength !== 32 || signatureBytes.byteLength !== 64) {
      throw new PublicPairMediaContractError('public_media_contract_signature_invalid');
    }
    const derivedKeyId = `rv:${(await sha256HexBytes(publicKeyBytes)).slice(0, 24)}`;
    if (derivedKeyId !== options.authorityKeyId) {
      throw new PublicPairMediaContractError('public_media_contract_authority_invalid');
    }
    const signed = { ...value };
    delete signed['signature_b64'];
    const publicKey = await crypto.subtle.importKey(
      'raw', arrayBuffer(publicKeyBytes), { name: 'Ed25519' }, false, ['verify'],
    );
    const verified = await crypto.subtle.verify(
      'Ed25519', publicKey, arrayBuffer(signatureBytes),
      arrayBuffer(new TextEncoder().encode(canonicalSecurityJson(signed))),
    );
    if (!verified) throw new PublicPairMediaContractError('public_media_contract_signature_invalid');
  } catch (error) {
    if (error instanceof PublicPairMediaContractError) throw error;
    throw new PublicPairMediaContractError('public_media_contract_signature_invalid');
  }
}

function closedObject(
  raw: unknown,
  expected: readonly string[],
  reasonCode: string,
): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new PublicPairMediaContractError(reasonCode);
  }
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value);
  if (keys.length !== expected.length || expected.some(field => !(field in value))) {
    throw new PublicPairMediaContractError(reasonCode);
  }
  return value;
}

function exactStrings(value: unknown, expected: readonly string[]): boolean {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && IDENTIFIER_RE.test(value);
}

async function sha256Hex(value: string): Promise<string> {
  return sha256HexBytes(new TextEncoder().encode(value));
}

async function sha256HexBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', arrayBuffer(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = Uint8Array.from(value);
  return copy.buffer;
}
