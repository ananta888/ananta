import { Injector, runInInjectionContext } from '@angular/core';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  PairMediaE2eeCoordinatorService,
  PairMediaE2eeTransportPort,
} from './pair-media-e2ee-coordinator.service';
import { PairMediaE2eeTransformAdapter, PublicPairMediaSlotKeySet } from './pair-media-e2ee-transform.adapter';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import {
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaSecurityContractV2,
} from './public-pair-media-security-contract';
import { SemanticDataChannelMessage } from './webrtc-datachannel.service';
import { VerifiedPeerBinding, WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { decodeB64, encodeB64 } from './webrtc-secure-envelope';

interface CoordinatorNode {
  readonly coordinator: PairMediaE2eeCoordinatorService;
  readonly transforms: CoordinatorTransforms;
  readonly crypto: TransparentEncryption;
  readonly binding: VerifiedPeerBinding;
  readonly disabled: ReturnType<typeof vi.fn>;
  readonly closed: ReturnType<typeof vi.fn>;
  readonly outbound: SemanticDataChannelMessage[];
  readonly sent: SemanticDataChannelMessage[];
  portOpen: boolean;
  portOpenError: Error | null;
}

describe('PairMediaE2eeCoordinatorService', () => {
  const activeNodes: CoordinatorNode[] = [];

  afterEach(() => {
    for (const node of activeNodes.splice(0)) {
      node.coordinator.deactivate('session-a', 'test_cleanup');
    }
    vi.useRealTimers();
  });

  it('prepares bilateral E2EE without capture consent and derives inverse keys for all fixed slots', async () => {
    const owner = node('peer:owner', 'peer:guest');
    const guest = node('peer:guest', 'peer:owner');
    connect(owner, guest);
    prepareNegotiation(owner);
    prepareNegotiation(guest);

    await pump(owner, guest);

    expect(owner.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(guest.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(owner.coordinator.publicationContextFor('session-a')).toEqual({
      sessionId: 'session-a', securityEpoch: 7, contractDigest: 'a'.repeat(64),
      adapterGeneration: 1, localPeerId: 'peer:owner', remotePeerId: 'peer:guest',
      maxExpiresAtMs: 2_000_000_000_000,
    });
    const ownerKeys = owner.transforms.installed[0];
    const guestKeys = guest.transforms.installed[0];
    expect(ownerKeys.map(value => value.slot)).toEqual(PUBLIC_PAIR_MEDIA_SLOTS.map(value => value.slot));
    expect(guestKeys.map(value => value.slot)).toEqual(PUBLIC_PAIR_MEDIA_SLOTS.map(value => value.slot));
    for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
      const ownerSlot = ownerKeys.find(value => value.slot === definition.slot)!;
      const guestSlot = guestKeys.find(value => value.slot === definition.slot)!;
      expect(ownerSlot.sendContext).toMatchObject({
        senderId: 'peer:owner', recipientId: 'peer:guest', slot: definition.slot,
        kind: definition.kind, codec: definition.codec,
      });
      expect(ownerSlot.receiveContext).toEqual(guestSlot.sendContext);
      expect(ownerSlot.sendContext).toEqual(guestSlot.receiveContext);
      expect(ownerSlot.sendContext.connectionId).toBe(ownerSlot.receiveContext.connectionId);
    }
  });

  it('reconciles an already-open consent port without an explicit open event', async () => {
    const owner = node('peer:owner', 'peer:guest');
    const guest = node('peer:guest', 'peer:owner');
    owner.portOpen = true;
    guest.portOpen = true;
    connect(owner, guest);
    owner.coordinator.markTopologyNegotiated('session-a');
    guest.coordinator.markTopologyNegotiated('session-a');
    expect(owner.coordinator.canActivate('session-a')).toBe(true);

    await pump(owner, guest);

    expect(owner.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(guest.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(countControl(owner, 'hello')).toBe(1);
    expect(countControl(guest, 'hello')).toBe(1);
  });

  it('arms bilateral preparation when activation admission observes a missed late open edge', async () => {
    const owner = node('peer:owner', 'peer:guest');
    const guest = node('peer:guest', 'peer:owner');
    connect(owner, guest);
    owner.coordinator.markTopologyNegotiated('session-a');
    guest.coordinator.markTopologyNegotiated('session-a');
    expect(owner.outbound).toEqual([]);
    expect(guest.outbound).toEqual([]);

    owner.portOpen = true;
    guest.portOpen = true;
    expect(owner.coordinator.canActivate('session-a')).toBe(true);
    expect(guest.coordinator.canActivate('session-a')).toBe(true);

    await pump(owner, guest);

    expect(owner.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(guest.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(countControl(owner, 'hello')).toBe(1);
    expect(countControl(guest, 'hello')).toBe(1);
  });

  it('keeps activation pending while the consent port remains closed', async () => {
    const owner = node('peer:owner', 'peer:guest');
    bindSink(owner);
    owner.coordinator.markTopologyNegotiated('session-a');
    expect(owner.coordinator.canActivate('session-a')).toBe(false);

    const activation = owner.coordinator.activate('session-a');
    await settle(4);

    expect(owner.outbound).toEqual([]);
    expect(owner.coordinator.publicationContextFor('session-a')).toBeNull();
    expect(owner.coordinator.statusFor('session-a')).toMatchObject({
      state: 'awaiting-peer', reasonCode: 'public_media_technical_preparation_pending',
    });

    owner.coordinator.deactivate('session-a', 'test_cleanup');
    await expect(activation).resolves.toMatchObject({ state: 'inactive', reasonCode: 'test_cleanup' });
  });

  it('fails closed when consent-port readiness cannot be read', async () => {
    const owner = node('peer:owner', 'peer:guest');
    owner.portOpenError = new Error('public_media_consent_channel_state_failed');
    bindSink(owner);
    await settle();
    expect(owner.coordinator.statusFor('session-a')).toMatchObject({
      state: 'failed', reasonCode: 'public_media_consent_channel_state_failed',
    });
    expect(owner.closed).toHaveBeenCalledWith('public_media_consent_channel_state_failed');
    expect(owner.transforms.prepared).toBe(false);
  });

  it('binds the exact v2 frame format into the encrypted bilateral hello', async () => {
    const owner = node('peer:owner', 'peer:guest');
    bindSink(owner);
    prepareNegotiation(owner);

    void owner.coordinator.activate('session-a');
    await settle(6);

    const hello = decodeControl(owner.outbound[0]);
    expect(hello).toMatchObject({
      schema: 'ananta.public-pair.media-hello.v2',
      kind: 'hello',
      frame_format: 'ananta.public-pair.media-frame.v2',
      media_contract_digest: 'a'.repeat(64),
    });
  });

  it('returns genuine failure immediately but resolves deactivate as inactive', async () => {
    const failing = node('peer:owner', 'peer:guest');
    bindSink(failing);
    prepareNegotiation(failing);
    const failedActivation = failing.coordinator.activate('session-a');
    failing.coordinator.fail('session-a', 'public_media_control_invalid');
    await expect(failedActivation).resolves.toMatchObject({
      state: 'failed', reasonCode: 'public_media_control_invalid',
    });

    const cancelled = node('peer:owner', 'peer:guest');
    bindSink(cancelled);
    prepareNegotiation(cancelled);
    const cancelledActivation = cancelled.coordinator.activate('session-a');
    cancelled.coordinator.deactivate('session-a', 'ordinary_media_capability_revoked');
    await expect(cancelledActivation).resolves.toMatchObject({
      state: 'inactive', reasonCode: 'ordinary_media_capability_revoked',
    });
  });

  it('refreshes expired pre-key hellos when the second peer becomes technically ready after the control TTL', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    const owner = node('peer:owner', 'peer:guest', 2_000_000);
    const guest = node('peer:guest', 'peer:owner', 2_000_000);
    connect(owner, guest);
    prepareNegotiation(owner);
    await pump(owner, guest);
    expect(countControl(owner, 'hello')).toBe(1);
    expect(owner.coordinator.statusFor('session-a').state).not.toBe('ready');

    await vi.advanceTimersByTimeAsync(31_000);
    prepareNegotiation(guest);
    await pump(owner, guest);

    expect(guest.coordinator.statusFor('session-a')).toMatchObject({ state: 'ready' });
    expect(owner.coordinator.statusFor('session-a').state).toBe('ready');
    expect(countControl(owner, 'hello')).toBe(2);
  });

  it('treats media failure after local ACK release as transport-fatal', async () => {
    const owner = node('peer:owner', 'peer:guest');
    const guest = node('peer:guest', 'peer:owner');
    connect(owner, guest);
    prepareNegotiation(owner);
    prepareNegotiation(guest);
    // Hold the guest ACK in the queue. Owner sends its ACK first, so the peer
    // may already be deriving even though owner has not installed locally.
    void owner.coordinator.activate('session-a');
    void guest.coordinator.activate('session-a');
    await settle(6);
    await deliverOne(owner, guest); // owner hello
    await deliverOne(guest, owner); // guest hello
    await settle(8);
    expect(countControl(owner, 'hello_ack')).toBe(1);

    owner.coordinator.failMediaExtension('session-a', 'media_e2ee_worker_failed');

    expect(owner.closed).toHaveBeenCalledWith('media_e2ee_worker_failed');
    expect(owner.disabled).not.toHaveBeenCalled();
  });

  it('downgrades a provably pre-ACK asymmetric topology to data-only', () => {
    const owner = node('peer:owner', 'peer:guest');
    bindSink(owner);
    owner.transforms.topologyError = new Error('public_media_topology_invalid');

    owner.coordinator.markTopologyNegotiated('session-a');

    expect(owner.disabled).toHaveBeenCalledWith('public_media_topology_invalid');
    expect(owner.closed).not.toHaveBeenCalled();
    expect(owner.coordinator.statusFor('session-a')).toMatchObject({
      state: 'failed', reasonCode: 'public_media_topology_invalid',
    });
  });

  it('fences a delayed worker ACK from a new adapter/runtime generation of the same session', async () => {
    const owner = node('peer:owner', 'peer:guest');
    const guest = node('peer:guest', 'peer:owner');
    const installGate = deferred<void>();
    const installEntered = deferred<void>();
    owner.transforms.installGate = installGate.promise;
    owner.transforms.installEntered = installEntered;
    connect(owner, guest);
    prepareNegotiation(owner);
    prepareNegotiation(guest);
    void owner.coordinator.activate('session-a');
    void guest.coordinator.activate('session-a');
    const oldHandshake = pump(owner, guest);
    await installEntered.promise;

    owner.coordinator.unbindTransport('session-a', 'test_reconnect');
    owner.transforms.generation = 2;
    owner.transforms.prepared = true;
    owner.closed.mockClear();
    bindSink(owner);
    installGate.resolve(undefined);
    await oldHandshake;
    await settle(5);

    expect(owner.transforms.installed).toEqual([]);
    expect(owner.closed).not.toHaveBeenCalled();
    expect(owner.coordinator.statusFor('session-a').state).toBe('inactive');
  });
});

function node(localPeerId: string, remotePeerId: string, expiresAtMs = 2_000_000_000_000): CoordinatorNode {
  const mediaContract = contract(expiresAtMs);
  const binding = {
    scopeKind: 'session', scopeId: 'session-a', localPeerId, remotePeerId,
    peerPublicKeySpkiB64: 'unused', epoch: 7, keyId: 'pair-key-7', contractDigest: 'b'.repeat(64),
    packageId: 'c'.repeat(64), tenantId: 'tenant', deviceId: 'device', membershipId: localPeerId,
    membershipVersion: 1, peerFingerprint: 'd'.repeat(64), confirmed: true,
    fingerprintChanged: false, transcriptDigest: 'e'.repeat(64), authorityKeyId: 'authority',
  } as VerifiedPeerBinding;
  const transforms = new CoordinatorTransforms();
  const encryption = new TransparentEncryption();
  const sequence = new Sequence();
  const injector = Injector.create({ providers: [
    { provide: PairViewSecurityBootstrapService, useValue: { mediaContractFor: () => mediaContract } },
    { provide: WebrtcPeerKeyService, useValue: { requireBinding: () => binding } },
    { provide: E2eEncryptionService, useValue: encryption },
    { provide: PairSecureSequenceService, useValue: sequence },
    { provide: PairMediaE2eeTransformAdapter, useValue: transforms },
  ] });
  const coordinator = runInInjectionContext(injector, () => new PairMediaE2eeCoordinatorService());
  const value: CoordinatorNode = {
    coordinator, transforms, crypto: encryption, binding,
    disabled: vi.fn(), closed: vi.fn(), outbound: [], sent: [],
    portOpen: false, portOpenError: null,
  };
  activeNode(value);
  return value;
}

const registeredNodes: CoordinatorNode[] = [];
function activeNode(value: CoordinatorNode): void {
  // The describe-local cleanup array is not accessible from helpers; release
  // long expiry timers through an afterEach registry instead.
  registeredNodes.push(value);
}

afterEach(() => {
  for (const value of registeredNodes.splice(0)) {
    value.coordinator.deactivate('session-a', 'test_cleanup');
  }
});

function connect(left: CoordinatorNode, right: CoordinatorNode): void {
  left.coordinator.bindTransport('session-a', port(left));
  right.coordinator.bindTransport('session-a', port(right));
}

function bindSink(value: CoordinatorNode): void {
  value.coordinator.bindTransport('session-a', port(value));
}

function port(value: CoordinatorNode): PairMediaE2eeTransportPort {
  return {
    isOpen: () => {
      if (value.portOpenError) throw value.portOpenError;
      return value.portOpen;
    },
    send: async message => { value.outbound.push(message); value.sent.push(message); },
    disableMedia: value.disabled,
    failClosed: value.closed,
  };
}

function prepareNegotiation(value: CoordinatorNode): void {
  value.coordinator.markDataChannelOpen('session-a');
  value.coordinator.markTopologyNegotiated('session-a');
}

async function pump(left: CoordinatorNode, right: CoordinatorNode): Promise<void> {
  for (let pass = 0; pass < 30; pass += 1) {
    await settle(3);
    let delivered = false;
    while (left.outbound.length) {
      delivered = true;
      await right.coordinator.acceptSemantic(left.outbound.shift()!);
    }
    while (right.outbound.length) {
      delivered = true;
      await left.coordinator.acceptSemantic(right.outbound.shift()!);
    }
    if (
      left.coordinator.statusFor('session-a').state === 'ready'
      && right.coordinator.statusFor('session-a').state === 'ready'
    ) return;
    if (!delivered) await settle(3);
  }
}

async function deliverOne(source: CoordinatorNode, recipient: CoordinatorNode): Promise<void> {
  await settle(3);
  const message = source.outbound.shift();
  if (!message) throw new Error('test_control_message_missing');
  await recipient.coordinator.acceptSemantic(message);
}

class CoordinatorTransforms {
  generation = 1;
  prepared = true;
  readonly installed: Array<readonly PublicPairMediaSlotKeySet[]> = [];
  topologyError: Error | null = null;
  installGate: Promise<void> | null = null;
  installEntered: { resolve(value: void): void } | null = null;
  isPrepared(_session: string, _epoch?: number, _digest?: string, generation?: number): boolean {
    return this.prepared && (generation === undefined || generation === this.generation);
  }
  isKeyed(session: string, epoch?: number, digest?: string, generation?: number): boolean {
    return this.installed.length > 0 && this.isPrepared(session, epoch, digest, generation);
  }
  generationForSession(): number | null { return this.prepared ? this.generation : null; }
  validateFinalTopology(): void { if (this.topologyError) throw this.topologyError; }
  async installKeys(_session: string, keys: readonly PublicPairMediaSlotKeySet[], generation: number): Promise<void> {
    this.installEntered?.resolve(undefined);
    if (this.installGate) await this.installGate;
    if (!this.isPrepared('session-a', undefined, undefined, generation)) throw new Error('stale_adapter');
    this.installed.push(keys);
  }
  releaseSession(_session?: string, generation?: number): void {
    if (generation === undefined || generation === this.generation) this.prepared = false;
  }
}

class Sequence {
  private value = 0;
  async next(): Promise<number> { return ++this.value; }
}

class TransparentEncryption {
  readonly derived: string[] = [];
  async seal(binding: VerifiedPeerBinding, plaintext: Uint8Array, options: any): Promise<any> {
    return {
      version: 1,
      scope: { kind: 'session', id: binding.scopeId },
      sender_id: binding.localPeerId,
      recipient: { kind: 'peer', id: binding.remotePeerId },
      epoch: binding.epoch,
      sequence: options.sequence,
      key_id: binding.keyId,
      payload_type: options.payloadType,
      expires_at_ms: options.expiresAtMs,
      nonce_b64: encodeB64(new Uint8Array(12)),
      aad: { traffic_class: options.trafficClass, content_encoding: 'binary', contract_digest: binding.contractDigest },
      ciphertext_b64: encodeB64(plaintext),
    };
  }
  async open(_binding: VerifiedPeerBinding, envelope: any): Promise<any> {
    return { envelope, plaintext: decodeB64(envelope.ciphertext_b64) };
  }
  async derivePurposeAesKey(_binding: VerifiedPeerBinding, _purpose: string, bindingId: string): Promise<CryptoKey> {
    this.derived.push(bindingId);
    return crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  }
}

function contract(expiresAtMs: number): PublicPairMediaSecurityContractV2 {
  return {
    domain: 'ananta.public-pair.media-security-contract.v2', version: 2,
    session_id: 'session-a', epoch: 7, digest: 'a'.repeat(64), expires_at_ms: expiresAtMs,
    transform: 'RTCRtpScriptTransform',
    frame_format: 'ananta.public-pair.media-frame.v2',
  } as PublicPairMediaSecurityContractV2;
}

function countControl(value: CoordinatorNode, kind: 'hello' | 'hello_ack'): number {
  return value.sent.filter(message => message.message_id.includes(`pair-media-${kind}-`)).length;
}

function decodeControl(message: SemanticDataChannelMessage | undefined): Record<string, unknown> {
  if (!message) throw new Error('test_control_message_missing');
  const envelope = JSON.parse(new TextDecoder().decode(decodeB64(message.ciphertext))) as {
    ciphertext_b64: string;
  };
  return JSON.parse(new TextDecoder().decode(decodeB64(envelope.ciphertext_b64))) as Record<string, unknown>;
}

async function settle(turns = 3): Promise<void> {
  for (let index = 0; index < turns; index += 1) {
    // Native WebCrypto completion is not guaranteed to run within a chain of
    // Promise.resolve() microtasks in Node.
    await crypto.subtle.digest('SHA-256', new Uint8Array(0));
  }
}

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}
