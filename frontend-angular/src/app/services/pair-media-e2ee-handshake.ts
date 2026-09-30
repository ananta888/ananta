/**
 * Wire contract of the Public Pair media E2EE handshake (hello / hello-ack
 * v2), its closed-schema validators and the canonical SHA-256 digests that
 * bind connection and frame keys. Pure functions over an explicit runtime
 * snapshot; PairMediaE2eeCoordinatorService owns all state and transport.
 */
import { PairMediaE2eeTransportPort } from './pair-media-e2ee-coordinator.types';
import { PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 } from './pair-media-frame-format';
import {
  PUBLIC_PAIR_MEDIA_GRANTS,
  PublicPairMediaSecurityContractV2,
} from './public-pair-media-security-contract';
import { VerifiedPeerBinding } from './webrtc-peer-key.service';
import {
  canonicalSecurityJson,
  decodeB64,
  encodeB64,
} from './webrtc-secure-envelope';

const DIGEST_RE = /^[a-f0-9]{64}$/;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;

export interface MediaHelloV2 {
  readonly schema: 'ananta.public-pair.media-hello.v2';
  readonly kind: 'hello';
  readonly session_id: string;
  readonly epoch: number;
  readonly sender_id: string;
  readonly recipient_id: string;
  readonly media_contract_digest: string;
  readonly frame_format: typeof PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2;
  readonly connection_salt_b64: string;
  readonly slots: typeof PUBLIC_PAIR_MEDIA_GRANTS;
  readonly expires_at_ms: number;
}

export interface MediaHelloAckV2 {
  readonly schema: 'ananta.public-pair.media-hello-ack.v2';
  readonly kind: 'hello_ack';
  readonly session_id: string;
  readonly epoch: number;
  readonly sender_id: string;
  readonly recipient_id: string;
  readonly media_contract_digest: string;
  readonly frame_format: typeof PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2;
  readonly hello_digests: readonly [
    Readonly<{ peer_id: string; digest: string }>,
    Readonly<{ peer_id: string; digest: string }>,
  ];
  readonly expires_at_ms: number;
}

export interface PairMediaE2eeRuntime {
  readonly sessionId: string;
  readonly generation: number;
  readonly adapterGeneration: number;
  readonly contract: Readonly<PublicPairMediaSecurityContractV2>;
  readonly binding: Readonly<VerifiedPeerBinding>;
  port: PairMediaE2eeTransportPort | null;
  dataChannelOpen: boolean;
  topologyReady: boolean;
  preparationArmed: boolean;
  localHello: MediaHelloV2 | null;
  localHelloDigest: string;
  remoteHello: MediaHelloV2 | null;
  remoteHelloDigest: string;
  localAck: MediaHelloAckV2 | null;
  remoteAck: MediaHelloAckV2 | null;
  localAckSent: boolean;
  helloSendPromise: Promise<void> | null;
  ackSendPromise: Promise<void> | null;
  installPromise: Promise<void> | null;
  installAttempted: boolean;
  peerMayBeReady: boolean;
  installed: boolean;
  failed: boolean;
  poisoned: boolean;
  expiryTimer: ReturnType<typeof setTimeout> | null;
}

export function parseHello(
  raw: unknown,
  runtime: PairMediaE2eeRuntime,
  direction: 'inbound' | 'outbound',
): MediaHelloV2 {
  const value = closedObject(raw, [
    'schema', 'kind', 'session_id', 'epoch', 'sender_id', 'recipient_id',
    'media_contract_digest', 'frame_format', 'connection_salt_b64', 'slots', 'expires_at_ms',
  ], 'public_media_hello_invalid');
  if (
    value['schema'] !== 'ananta.public-pair.media-hello.v2'
    || value['kind'] !== 'hello'
    || value['session_id'] !== runtime.sessionId
    || value['epoch'] !== runtime.contract.epoch
    || value['media_contract_digest'] !== runtime.contract.digest
    || value['frame_format'] !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
    || !exactStrings(value['slots'], PUBLIC_PAIR_MEDIA_GRANTS)
    || !validControlExpiry(value['expires_at_ms'], runtime.contract.expires_at_ms)
  ) throw new Error('public_media_hello_invalid');
  validateDirectedPeers(value, runtime, direction);
  const salt = decodeB64(value['connection_salt_b64']);
  if (salt.byteLength !== 16 || encodeB64(salt) !== value['connection_salt_b64']) {
    throw new Error('public_media_hello_invalid');
  }
  return Object.freeze(value as unknown as MediaHelloV2);
}

export function parseAck(
  raw: unknown,
  runtime: PairMediaE2eeRuntime,
  direction: 'inbound' | 'outbound',
): MediaHelloAckV2 {
  const value = closedObject(raw, [
    'schema', 'kind', 'session_id', 'epoch', 'sender_id', 'recipient_id',
    'media_contract_digest', 'frame_format', 'hello_digests', 'expires_at_ms',
  ], 'public_media_ack_invalid');
  if (
    value['schema'] !== 'ananta.public-pair.media-hello-ack.v2'
    || value['kind'] !== 'hello_ack'
    || value['session_id'] !== runtime.sessionId
    || value['epoch'] !== runtime.contract.epoch
    || value['media_contract_digest'] !== runtime.contract.digest
    || value['frame_format'] !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
    || !validControlExpiry(value['expires_at_ms'], runtime.contract.expires_at_ms)
  ) throw new Error('public_media_ack_invalid');
  validateDirectedPeers(value, runtime, direction);
  if (!Array.isArray(value['hello_digests']) || value['hello_digests'].length !== 2) {
    throw new Error('public_media_ack_invalid');
  }
  const rows = value['hello_digests'].map(item => {
    const row = closedObject(item, ['peer_id', 'digest'], 'public_media_ack_invalid');
    if (!ID_RE.test(String(row['peer_id'] ?? '')) || !DIGEST_RE.test(String(row['digest'] ?? ''))) {
      throw new Error('public_media_ack_invalid');
    }
    return Object.freeze(row as unknown as { peer_id: string; digest: string });
  });
  if (rows[0].peer_id >= rows[1].peer_id) throw new Error('public_media_ack_invalid');
  return Object.freeze({
    ...(value as unknown as MediaHelloAckV2),
    hello_digests: Object.freeze(rows) as MediaHelloAckV2['hello_digests'],
  });
}

export function validateDirectedPeers(
  value: Record<string, unknown>,
  runtime: PairMediaE2eeRuntime,
  direction: 'inbound' | 'outbound',
): void {
  const sender = value['sender_id'];
  const recipient = value['recipient_id'];
  const outbound = sender === runtime.binding.localPeerId && recipient === runtime.binding.remotePeerId;
  const inbound = sender === runtime.binding.remotePeerId && recipient === runtime.binding.localPeerId;
  if ((direction === 'outbound' && !outbound) || (direction === 'inbound' && !inbound)) {
    throw new Error('public_media_control_context_mismatch');
  }
}

export function helloDigestRows(runtime: PairMediaE2eeRuntime): MediaHelloAckV2['hello_digests'] {
  if (!runtime.localHelloDigest || !runtime.remoteHelloDigest) throw new Error('public_media_hello_missing');
  const rows = [
    Object.freeze({ peer_id: runtime.binding.localPeerId, digest: runtime.localHelloDigest }),
    Object.freeze({ peer_id: runtime.binding.remotePeerId, digest: runtime.remoteHelloDigest }),
  ].sort((left, right) => left.peer_id.localeCompare(right.peer_id));
  return Object.freeze(rows) as unknown as MediaHelloAckV2['hello_digests'];
}

export function assertFreshHandshake(runtime: PairMediaE2eeRuntime): void {
  const now = Date.now();
  if (
    !runtime.localHello
    || !runtime.remoteHello
    || !runtime.localAck
    || !runtime.remoteAck
    || runtime.localHello.expires_at_ms <= now
    || runtime.remoteHello.expires_at_ms <= now
    || runtime.localAck.expires_at_ms <= now
    || runtime.remoteAck.expires_at_ms <= now
    || canonicalSecurityJson(runtime.localAck.hello_digests)
      !== canonicalSecurityJson(helloDigestRows(runtime))
    || canonicalSecurityJson(runtime.remoteAck.hello_digests)
      !== canonicalSecurityJson(helloDigestRows(runtime))
  ) throw new Error('public_media_handshake_expired');
}

export async function connectionDigest(runtime: PairMediaE2eeRuntime): Promise<string> {
  if (!runtime.localHello || !runtime.remoteHello) throw new Error('public_media_hello_missing');
  const salts = [runtime.localHello, runtime.remoteHello]
    .map(hello => ({ peer_id: hello.sender_id, salt_b64: hello.connection_salt_b64 }))
    .sort((left, right) => left.peer_id.localeCompare(right.peer_id));
  return digestCanonical({
    domain: 'ananta.public-pair.media-connection.v2',
    session_id: runtime.sessionId,
    epoch: runtime.contract.epoch,
    media_contract_digest: runtime.contract.digest,
    frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
    salts,
  });
}

export function frameKeyBindingDigest(
  runtime: PairMediaE2eeRuntime,
  connectionId: string,
  senderId: string,
  recipientId: string,
  slot: string,
): Promise<string> {
  return digestCanonical({
    domain: 'ananta.public-pair.media-frame-key-binding.v2',
    session_id: runtime.sessionId,
    epoch: runtime.contract.epoch,
    media_contract_digest: runtime.contract.digest,
    frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
    connection_id: connectionId,
    sender_id: senderId,
    recipient_id: recipientId,
    slot,
  });
}

export function validControlExpiry(value: unknown, contractExpiry: number): boolean {
  return Number.isSafeInteger(value) && (value as number) > Date.now() && (value as number) <= contractExpiry;
}

export function closedObject(raw: unknown, fields: readonly string[], reasonCode: string): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(reasonCode);
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(field => !(field in value))) throw new Error(reasonCode);
  return value;
}

export function exactStrings(value: unknown, expected: readonly string[]): boolean {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

export function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message)
    ? error.message : fallback;
}

export function digestCanonical(value: unknown): Promise<string> {
  return digestBytes(new TextEncoder().encode(canonicalSecurityJson(value)));
}

export async function digestBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
