import { canonicalSecurityJson, decodeB64 } from '../webrtc-secure-envelope';

export type PeerOverlayDataClass = 'control' | 'rekey' | 'event' | 'semantic' | 'bulk';

export interface AcceptedPeerRouteLease {
  readonly validation: 'hub-route-lease-accepted-v1';
  readonly leaseId: string;
  readonly tenantId: string;
  readonly roomId: string;
  readonly publicationId: string;
  readonly localPeerId: string;
  readonly childPeerIds: readonly string[];
  readonly routeEpoch: number;
  readonly maxHops: number;
  readonly expiresAtMs: number;
  readonly trafficClasses: readonly PeerOverlayDataClass[];
}

export interface OpaquePeerRelayPacketV1 {
  readonly version: 1;
  readonly message_id: string;
  readonly tenant_id: string;
  readonly room_id: string;
  readonly publication_id: string;
  readonly origin_peer_id: string;
  readonly destination_peer_id: string;
  readonly route_epoch: number;
  readonly traffic_class: PeerOverlayDataClass;
  readonly expires_at_ms: number;
  readonly hop_limit: number;
  readonly path: readonly string[];
  readonly chunk_index: number;
  readonly chunk_count: number;
  readonly ciphertext_digest: string;
  readonly ciphertext_b64: string;
  readonly signature_b64: string;
}

export interface PeerOverlayPacketAuthenticityPort {
  verify(originPeerId: string, signedPacket: Uint8Array, signature: Uint8Array): Promise<boolean>;
}

export interface PeerOverlayChildDataPort {
  readonly childPeerId: string;
  readonly bufferedAmount: number;
  readonly readyState: 'connecting' | 'open' | 'closing' | 'closed';
  send(packet: OpaquePeerRelayPacketV1): void;
}

export interface PeerOverlayRelayResult {
  readonly state: 'delivered_local' | 'queued' | 'rejected';
  readonly reasonCode: string;
  readonly childPeerId?: string;
}

interface QueuedPacket {
  readonly packet: OpaquePeerRelayPacketV1;
  readonly bytes: number;
}

const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$/;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const CLASSES: readonly PeerOverlayDataClass[] = ['control', 'rekey', 'event', 'semantic', 'bulk'];
const PRIORITY: readonly PeerOverlayDataClass[] = ['control', 'rekey', 'event', 'semantic', 'bulk'];
const CAPS: Readonly<Record<PeerOverlayDataClass, { messages: number; bytes: number }>> = Object.freeze({
  control: { messages: 128, bytes: 512 * 1024 },
  rekey: { messages: 64, bytes: 256 * 1024 },
  event: { messages: 128, bytes: 2 * 1024 * 1024 },
  semantic: { messages: 64, bytes: 4 * 1024 * 1024 },
  bulk: { messages: 32, bytes: 8 * 1024 * 1024 },
});
const MAX_REPLAY_ENTRIES = 4_096;
const MAX_CIPHERTEXT_BYTES = 256 * 1024;
const HIGH_WATER_BYTES = 2 * 1024 * 1024;

/** Relays opaque authenticated ciphertext and deliberately exposes no decrypt API. */
export class PeerOverlayDataRelay {
  private readonly children = new Map<string, PeerOverlayChildDataPort>();
  private readonly queues = new Map<string, Map<PeerOverlayDataClass, QueuedPacket[]>>();
  private readonly seenUntil = new Map<string, number>();
  private readonly drops = new Map<string, number>();

  constructor(
    private lease: AcceptedPeerRouteLease,
    private readonly clock: () => number = () => Date.now(),
    private readonly authenticity: PeerOverlayPacketAuthenticityPort = DENY_UNVERIFIED_PACKETS,
  ) {
    validateLease(lease, clock());
  }

  bindChild(port: PeerOverlayChildDataPort): void {
    if (!this.lease.childPeerIds.includes(port.childPeerId)) throw new Error('peer_overlay_child_not_leased');
    this.children.set(port.childPeerId, port);
    this.queues.set(port.childPeerId, new Map(CLASSES.map(value => [value, []])));
    this.flush(port.childPeerId);
  }

  unbindChild(childPeerId: string): void {
    this.children.delete(childPeerId);
    this.queues.delete(childPeerId);
  }

  replaceLease(lease: AcceptedPeerRouteLease): void {
    validateLease(lease, this.clock());
    if (lease.localPeerId !== this.lease.localPeerId || lease.publicationId !== this.lease.publicationId
        || lease.routeEpoch <= this.lease.routeEpoch) throw new Error('peer_overlay_lease_transition_invalid');
    this.lease = lease;
    for (const child of [...this.children.keys()]) if (!lease.childPeerIds.includes(child)) this.unbindChild(child);
  }

  async accept(raw: unknown): Promise<PeerOverlayRelayResult> {
    const now = this.clock();
    this.purgeReplay(now);
    let packet: OpaquePeerRelayPacketV1;
    try { packet = await parsePacket(raw, this.lease, now, this.authenticity); } catch (error) {
      return this.reject(error instanceof Error ? error.message : 'peer_overlay_packet_invalid');
    }
    const replayKey = `${packet.origin_peer_id}\0${packet.publication_id}\0${packet.route_epoch}\0${packet.message_id}\0${packet.chunk_index}`;
    if (this.seenUntil.has(replayKey)) return this.reject('peer_overlay_duplicate');
    if (this.seenUntil.size >= MAX_REPLAY_ENTRIES) return this.reject('peer_overlay_replay_budget_exceeded');
    this.seenUntil.set(replayKey, packet.expires_at_ms);
    if (packet.destination_peer_id === this.lease.localPeerId) {
      return Object.freeze({ state: 'delivered_local', reasonCode: 'peer_overlay_destination_reached' });
    }
    if (!this.lease.childPeerIds.includes(packet.destination_peer_id)) {
      return this.reject('peer_overlay_destination_not_leased');
    }
    const forwarded = Object.freeze({
      ...packet,
      hop_limit: packet.hop_limit - 1,
      path: Object.freeze([...packet.path, this.lease.localPeerId]),
    });
    if (forwarded.hop_limit < 0) return this.reject('peer_overlay_hop_limit_exhausted');
    const queued = this.enqueue(packet.destination_peer_id, forwarded);
    if (!queued) return this.reject('peer_overlay_child_queue_overloaded');
    this.flush(packet.destination_peer_id);
    return Object.freeze({ state: 'queued', reasonCode: 'peer_overlay_queued', childPeerId: packet.destination_peer_id });
  }

  flush(childPeerId: string): number {
    const port = this.children.get(childPeerId);
    const lanes = this.queues.get(childPeerId);
    if (!port || !lanes || port.readyState !== 'open') return 0;
    let sent = 0;
    for (const trafficClass of PRIORITY) {
      const queue = lanes.get(trafficClass)!;
      while (queue.length && port.bufferedAmount <= HIGH_WATER_BYTES) {
        const item = queue.shift()!;
        if (item.packet.expires_at_ms <= this.clock()) { this.drop('peer_overlay_packet_expired'); continue; }
        try { port.send(item.packet); sent += 1; } catch { this.drop('peer_overlay_child_send_failed'); break; }
      }
    }
    return sent;
  }

  snapshot(): Readonly<Record<string, unknown>> {
    const queueDepths: Record<string, Record<string, number>> = {};
    for (const [child, lanes] of this.queues) {
      queueDepths[child] = Object.fromEntries(CLASSES.map(value => [value, lanes.get(value)!.length]));
    }
    return Object.freeze({
      publicationId: this.lease.publicationId,
      routeEpoch: this.lease.routeEpoch,
      children: Object.freeze([...this.children.keys()].sort()),
      queueDepths: Object.freeze(queueDepths),
      drops: Object.freeze(Object.fromEntries(this.drops)),
      replayEntries: this.seenUntil.size,
    });
  }

  private enqueue(childPeerId: string, packet: OpaquePeerRelayPacketV1): boolean {
    const lanes = this.queues.get(childPeerId);
    if (!lanes) return false;
    const queue = lanes.get(packet.traffic_class)!;
    const bytes = decodeB64(packet.ciphertext_b64).byteLength;
    const cap = CAPS[packet.traffic_class];
    if (queue.length >= cap.messages || queue.reduce((sum, item) => sum + item.bytes, 0) + bytes > cap.bytes) return false;
    queue.push({ packet, bytes });
    return true;
  }

  private purgeReplay(now: number): void {
    for (const [key, expiresAt] of this.seenUntil) if (expiresAt <= now) this.seenUntil.delete(key);
  }

  private reject(reasonCode: string): PeerOverlayRelayResult {
    this.drop(reasonCode);
    return Object.freeze({ state: 'rejected', reasonCode });
  }

  private drop(reasonCode: string): void { this.drops.set(reasonCode, (this.drops.get(reasonCode) ?? 0) + 1); }
}

async function parsePacket(
  raw: unknown,
  lease: AcceptedPeerRouteLease,
  now: number,
  authenticity: PeerOverlayPacketAuthenticityPort,
): Promise<OpaquePeerRelayPacketV1> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('peer_overlay_packet_invalid');
  const value = raw as Record<string, unknown>;
  const expected = [
    'version', 'message_id', 'tenant_id', 'room_id', 'publication_id', 'origin_peer_id',
    'destination_peer_id', 'route_epoch', 'traffic_class', 'expires_at_ms', 'hop_limit',
    'path', 'chunk_index', 'chunk_count', 'ciphertext_digest', 'ciphertext_b64', 'signature_b64',
  ];
  if (Object.keys(value).some(key => !expected.includes(key)) || expected.some(key => !(key in value))) {
    throw new Error('peer_overlay_packet_fields_invalid');
  }
  for (const field of ['message_id', 'tenant_id', 'room_id', 'publication_id', 'origin_peer_id', 'destination_peer_id']) {
    if (typeof value[field] !== 'string' || !ID_RE.test(String(value[field]))) throw new Error('peer_overlay_packet_id_invalid');
  }
  if (value['version'] !== 1 || value['tenant_id'] !== lease.tenantId || value['room_id'] !== lease.roomId
      || value['publication_id'] !== lease.publicationId || value['route_epoch'] !== lease.routeEpoch) {
    throw new Error('peer_overlay_packet_scope_invalid');
  }
  if (!lease.trafficClasses.includes(value['traffic_class'] as PeerOverlayDataClass)) {
    throw new Error('peer_overlay_traffic_class_denied');
  }
  const expiresAt = exactInteger(value['expires_at_ms'], 1, Number.MAX_SAFE_INTEGER);
  if (expiresAt <= now || expiresAt > now + 120_000 || lease.expiresAtMs <= now) throw new Error('peer_overlay_packet_expired');
  const hopLimit = exactInteger(value['hop_limit'], 0, lease.maxHops);
  const path = value['path'];
  if (!Array.isArray(path) || path.some(item => typeof item !== 'string' || !ID_RE.test(item))
      || new Set(path).size !== path.length || path.includes(lease.localPeerId)) throw new Error('peer_overlay_path_invalid');
  const chunkIndex = exactInteger(value['chunk_index'], 0, 255);
  const chunkCount = exactInteger(value['chunk_count'], 1, 256);
  if (chunkIndex >= chunkCount) throw new Error('peer_overlay_chunk_invalid');
  if (typeof value['ciphertext_digest'] !== 'string' || !DIGEST_RE.test(value['ciphertext_digest'])) {
    throw new Error('peer_overlay_ciphertext_digest_invalid');
  }
  const ciphertext = decodeB64(String(value['ciphertext_b64']));
  if (!ciphertext.byteLength || ciphertext.byteLength > MAX_CIPHERTEXT_BYTES) throw new Error('peer_overlay_ciphertext_size_invalid');
  const digestInput = new Uint8Array(ciphertext.byteLength);
  digestInput.set(ciphertext);
  const digest = await crypto.subtle.digest('SHA-256', digestInput);
  const hex = [...new Uint8Array(digest)].map(item => item.toString(16).padStart(2, '0')).join('');
  if (hex !== value['ciphertext_digest']) throw new Error('peer_overlay_ciphertext_digest_mismatch');
  const signature = decodeB64(String(value['signature_b64']));
  if (!signature.byteLength || signature.byteLength > 512) throw new Error('peer_overlay_signature_invalid');
  const { signature_b64: _signature, ...signedFields } = value;
  const signedPacket = new TextEncoder().encode(canonicalSecurityJson(signedFields));
  if (!await authenticity.verify(String(value['origin_peer_id']), signedPacket, signature)) {
    throw new Error('peer_overlay_signature_invalid');
  }
  return Object.freeze({
    ...(value as unknown as OpaquePeerRelayPacketV1),
    traffic_class: value['traffic_class'] as PeerOverlayDataClass,
    expires_at_ms: expiresAt,
    hop_limit: hopLimit,
    path: Object.freeze([...path] as string[]),
    chunk_index: chunkIndex,
    chunk_count: chunkCount,
  });
}

const DENY_UNVERIFIED_PACKETS: PeerOverlayPacketAuthenticityPort = Object.freeze({
  verify: async () => false,
});

function validateLease(value: AcceptedPeerRouteLease, now: number): void {
  if (value.validation !== 'hub-route-lease-accepted-v1' || value.expiresAtMs <= now
      || value.routeEpoch < 1 || value.maxHops < 1 || value.maxHops > 8
      || new Set(value.childPeerIds).size !== value.childPeerIds.length
      || value.childPeerIds.includes(value.localPeerId)) throw new Error('peer_overlay_lease_invalid');
}

function exactInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new Error('peer_overlay_integer_invalid');
  }
  return Number(value);
}
