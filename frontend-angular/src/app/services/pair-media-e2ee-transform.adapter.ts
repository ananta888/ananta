import { Injectable, InjectionToken, inject } from '@angular/core';

import type { PairMediaE2eeFrameContext } from './pair-media-e2ee-frame-codec';
import { PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 } from './pair-media-frame-format';
import {
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaSecurityContractV2,
  PublicPairMediaSlot,
} from './public-pair-media-security-contract';

export interface PublicPairMediaSlotKeySet {
  readonly slot: PublicPairMediaSlot;
  readonly sendKey: CryptoKey;
  readonly receiveKey: CryptoKey;
  readonly sendContext: PairMediaE2eeFrameContext;
  readonly receiveContext: PairMediaE2eeFrameContext;
}

export interface PublicPairMediaOutboundPublicationGate {
  readonly revision: number;
  readonly enabled: boolean;
  readonly slots: readonly PublicPairMediaSlot[];
  readonly expiresAtMs: number;
}

export type PairMediaE2eeWorkerFactory = () => Worker;

export const PAIR_MEDIA_E2EE_WORKER_FACTORY = new InjectionToken<PairMediaE2eeWorkerFactory>(
  'PAIR_MEDIA_E2EE_WORKER_FACTORY',
  {
    providedIn: 'root',
    factory: () => () => new Worker(
      new URL('../workers/pair-media-e2ee.worker', import.meta.url),
      { type: 'module', name: 'ananta-public-pair-media-e2ee' },
    ),
  },
);

interface SlotTransformState {
  readonly slot: PublicPairMediaSlot;
  readonly transceiver: RTCRtpTransceiver;
  readonly sendTransformId: string;
  readonly receiveTransformId: string;
}

interface AdapterState {
  readonly sessionId: string;
  readonly generation: number;
  readonly epoch: number;
  readonly contractDigest: string;
  readonly contractExpiresAtMs: number;
  readonly peer: RTCPeerConnection;
  readonly worker: Worker;
  readonly slots: Map<PublicPairMediaSlot, SlotTransformState>;
  readonly receiverSlots: WeakMap<RTCRtpReceiver, PublicPairMediaSlot>;
  readonly slotReadiness: Map<PublicPairMediaSlot, Promise<void>>;
  readonly pending: Map<string, Deferred<void>>;
  readonly onFatal: (reasonCode: string) => void;
  readonly role: 'offerer' | 'answerer';
  keyed: boolean;
  everInstalled: boolean;
  failed: boolean;
  remoteTopologyFinalized: boolean;
  publicationGateTail: Promise<void>;
}

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value?: T): void;
  reject(error: unknown): void;
  readonly timeout: ReturnType<typeof setTimeout>;
}

const WORKER_ACK_TIMEOUT_MS = 5_000;

/** Standards-only RTCRtpScriptTransform adapter for one active Pair session. */
@Injectable({ providedIn: 'root' })
export class PairMediaE2eeTransformAdapter {
  private readonly createWorker = inject(PAIR_MEDIA_E2EE_WORKER_FACTORY);
  private active: AdapterState | null = null;
  private generationSerial = 0;

  isPrepared(sessionId: string, epoch?: number, contractDigest?: string, generation?: number): boolean {
    return this.active?.sessionId === sessionId
      && (epoch === undefined || this.active.epoch === epoch)
      && (contractDigest === undefined || this.active.contractDigest === contractDigest)
      && (generation === undefined || this.active.generation === generation)
      && !this.active.failed;
  }

  isKeyed(sessionId: string, epoch?: number, contractDigest?: string, generation?: number): boolean {
    return this.isPrepared(sessionId, epoch, contractDigest, generation) && this.active?.keyed === true;
  }

  generationForSession(sessionId: string): number | null {
    return this.isPrepared(sessionId) ? this.active!.generation : null;
  }

  async prepareSession(
    peer: RTCPeerConnection,
    sessionId: string,
    contract: Readonly<PublicPairMediaSecurityContractV2>,
    onFatal: (reasonCode: string) => void,
    role: 'offerer' | 'answerer' = 'offerer',
  ): Promise<number> {
    if (
      !sessionId
      || contract.version !== 2
      || contract.domain !== 'ananta.public-pair.media-security-contract.v2'
      || contract.session_id !== sessionId
      || contract.expires_at_ms <= Date.now()
      || contract.transform !== 'RTCRtpScriptTransform'
      || contract.frame_format !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
    ) throw new Error('public_media_contract_not_ready');
    if (
      this.active?.sessionId === sessionId
      && this.active.peer === peer
      && this.active.epoch === contract.epoch
      && this.active.contractDigest === contract.digest
      && this.active.contractExpiresAtMs === contract.expires_at_ms
      && this.active.role === role
      && !this.active.failed
    ) return this.active.generation;
    this.releaseSession();
    const worker = this.createWorker();
    const pending = new Map<string, Deferred<void>>();
    const receiverSlots = new WeakMap<RTCRtpReceiver, PublicPairMediaSlot>();
    const slots = new Map<PublicPairMediaSlot, SlotTransformState>();
    const slotReadiness = new Map<PublicPairMediaSlot, Promise<void>>();
    const state: AdapterState = {
      sessionId, generation: ++this.generationSerial,
      epoch: contract.epoch, contractDigest: contract.digest,
      contractExpiresAtMs: contract.expires_at_ms,
      peer, worker, pending, receiverSlots, slots, slotReadiness, onFatal, role,
      keyed: false, everInstalled: false, failed: false,
      remoteTopologyFinalized: role === 'offerer',
      publicationGateTail: Promise.resolve(),
    };
    this.active = state;
    worker.onmessage = event => this.handleWorkerMessage(state, event.data);
    worker.onerror = () => this.fail(state, 'media_e2ee_worker_failed');
    worker.onmessageerror = () => this.fail(state, 'media_e2ee_worker_message_invalid');
    try {
      if (role === 'offerer') {
        for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
          const transceiver = peer.addTransceiver(definition.kind, { direction: 'sendrecv' });
          await this.attachSlot(state, definition, transceiver);
        }
      }
      return state.generation;
    } catch (error) {
      this.fail(state, reason(error, 'media_e2ee_transform_prepare_failed'));
      throw error;
    }
  }

  /**
   * Binds the transceivers created from the remote offer. Precreating them on
   * the answerer duplicates m-lines in both Firefox and Chromium.
   */
  async bindRemoteOfferTopology(sessionId: string, generation: number): Promise<void> {
    const state = this.requireState(sessionId, generation);
    if (state.role !== 'answerer') {
      throw new Error('public_media_topology_invalid');
    }
    const transceivers = state.peer.getTransceivers();
    if (transceivers.length !== PUBLIC_PAIR_MEDIA_SLOTS.length) {
      throw new Error('public_media_topology_invalid');
    }
    const mids = new Set<string>();
    for (const [index, definition] of PUBLIC_PAIR_MEDIA_SLOTS.entries()) {
      const transceiver = transceivers[index];
      if (
        transceiver.receiver.track.kind !== definition.kind
        || !transceiver.mid
        || mids.has(transceiver.mid)
      ) throw new Error('public_media_topology_invalid');
      mids.add(transceiver.mid);
      transceiver.direction = 'sendrecv';
      const existing = state.slots.get(definition.slot);
      if (existing && existing.transceiver !== transceiver) {
        throw new Error('public_media_topology_invalid');
      }
      if (existing) setExactCodecPreference(transceiver, definition.kind, definition.codec);
      else this.attachSlot(state, definition, transceiver);
    }
    await Promise.all(PUBLIC_PAIR_MEDIA_SLOTS.map(definition => state.slotReadiness.get(definition.slot)));
    if (this.active !== state || state.failed) throw new Error('media_e2ee_session_released');
    state.remoteTopologyFinalized = true;
  }

  isAwaitingRemoteTopology(sessionId: string, generation?: number): boolean {
    return this.isPrepared(sessionId, undefined, undefined, generation)
      && this.active?.role === 'answerer'
      && !this.active.remoteTopologyFinalized;
  }

  /**
   * Called synchronously from `ontrack` while setRemoteDescription is still
   * applying the offer. Transform assignment occurs before this method
   * returns, satisfying the first-full-frame Insertable Streams boundary.
   */
  stageRemoteOfferTrack(
    sessionId: string,
    transceiver: RTCRtpTransceiver,
    receiver: RTCRtpReceiver,
    generation: number,
  ): PublicPairMediaSlot {
    const state = this.requireState(sessionId, generation);
    if (state.role !== 'answerer' || transceiver.receiver !== receiver) {
      throw new Error('public_media_topology_invalid');
    }
    const index = state.peer.getTransceivers().findIndex(value => value === transceiver);
    const definition = PUBLIC_PAIR_MEDIA_SLOTS[index];
    if (!definition || receiver.track.kind !== definition.kind || !transceiver.mid) {
      throw new Error('public_media_topology_invalid');
    }
    const existing = state.slots.get(definition.slot);
    if (existing && existing.transceiver !== transceiver) {
      throw new Error('public_media_topology_invalid');
    }
    if (!existing) {
      const readiness = this.attachSlot(state, definition, transceiver);
      void readiness.catch(error => this.fail(state, reason(error, 'media_e2ee_transform_prepare_failed')));
    }
    return definition.slot;
  }

  async installKeys(
    sessionId: string,
    keys: readonly PublicPairMediaSlotKeySet[],
    generation: number,
  ): Promise<void> {
    const state = this.requireState(sessionId, generation);
    if (state.everInstalled) throw new Error('media_e2ee_key_reinstall_forbidden');
    if (keys.length !== PUBLIC_PAIR_MEDIA_SLOTS.length) throw new Error('media_e2ee_key_set_invalid');
    const entries: Array<{ transformId: string; context: PairMediaE2eeFrameContext; key: CryptoKey }> = [];
    const keyObjects = new Set<CryptoKey>();
    let connectionId = '';
    let localPeerId = '';
    let remotePeerId = '';
    for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
      const values = keys.filter(value => value.slot === definition.slot);
      const slot = state.slots.get(definition.slot);
      if (values.length !== 1 || !slot) throw new Error('media_e2ee_key_set_invalid');
      const value = values[0];
      validateSlotContext(value.sendContext, definition.slot, definition.kind, definition.codec);
      validateSlotContext(value.receiveContext, definition.slot, definition.kind, definition.codec);
      if (
        value.sendContext.sessionId !== state.sessionId
        || value.receiveContext.sessionId !== state.sessionId
        || value.sendContext.keyEpoch !== state.epoch
        || value.receiveContext.keyEpoch !== state.epoch
        || value.sendContext.mediaContractDigest !== state.contractDigest
        || value.receiveContext.mediaContractDigest !== state.contractDigest
        || value.sendContext.contractExpiresAtMs !== state.contractExpiresAtMs
        || value.receiveContext.contractExpiresAtMs !== state.contractExpiresAtMs
      ) throw new Error('media_e2ee_key_set_binding_mismatch');
      if (
        value.sendContext.connectionId !== value.receiveContext.connectionId
        || value.sendContext.senderId !== value.receiveContext.recipientId
        || value.sendContext.recipientId !== value.receiveContext.senderId
        || value.sendKey === value.receiveKey
        || keyObjects.has(value.sendKey)
        || keyObjects.has(value.receiveKey)
      ) throw new Error('media_e2ee_key_set_direction_invalid');
      if (!connectionId) {
        connectionId = value.sendContext.connectionId;
        localPeerId = value.sendContext.senderId;
        remotePeerId = value.sendContext.recipientId;
      } else if (
        value.sendContext.connectionId !== connectionId
        || value.sendContext.senderId !== localPeerId
        || value.sendContext.recipientId !== remotePeerId
      ) throw new Error('media_e2ee_key_set_direction_invalid');
      keyObjects.add(value.sendKey);
      keyObjects.add(value.receiveKey);
      entries.push(
        { transformId: slot.sendTransformId, context: value.sendContext, key: value.sendKey },
        { transformId: slot.receiveTransformId, context: value.receiveContext, key: value.receiveKey },
      );
    }
    const installed = this.expect(state, `installed:${sessionId}`);
    // Once a key-install message can reach the worker this worker generation
    // is one-shot. Lost ACK/failure terminates it; it is never retried with a
    // reset counter under an ambiguous key context.
    state.everInstalled = true;
    state.worker.postMessage({ version: 1, type: 'install-keys', sessionId, entries });
    await installed;
    if (state.failed || this.active !== state) throw new Error('media_e2ee_worker_failed');
    state.keyed = true;
  }

  clearKeys(sessionId: string): void {
    const state = this.active;
    if (!state || state.sessionId !== sessionId || state.failed) return;
    state.keyed = false;
    state.worker.postMessage({ version: 1, type: 'clear-keys', sessionId });
  }

  async setOutboundPublicationGate(
    sessionId: string,
    adapterGeneration: number,
    gate: Readonly<PublicPairMediaOutboundPublicationGate>,
  ): Promise<void> {
    const state = this.requireState(sessionId, adapterGeneration);
    if (!state.keyed) throw new Error('public_media_transform_not_keyed');
    const normalized = normalizePublicationGate(gate, state.contractExpiresAtMs, true);
    const operation = state.publicationGateTail.then(async () => {
      if (this.active !== state || state.failed || !state.keyed) {
        throw new Error('public_media_transform_not_keyed');
      }
      assertPublicationGateLive(normalized);
      const pendingKey = publicationGatePendingKey(sessionId, normalized);
      const acknowledged = this.expect(state, pendingKey);
      try {
        state.worker.postMessage({
          version: 1,
          type: 'set-publication-gate',
          sessionId,
          gate: normalized,
        });
      } catch {
        this.fail(state, 'media_e2ee_worker_failed');
      }
      await acknowledged;
      if (
        this.active !== state
        || state.failed
        || !state.keyed
        || state.generation !== adapterGeneration
      ) throw new Error('media_e2ee_worker_failed');
    });
    // Serialize revisions so an async ACK cannot let a later gate overtake an
    // earlier one. Keep the tail observed; the returned operation still
    // preserves the exact failure for its caller.
    state.publicationGateTail = operation.catch(() => undefined);
    return operation;
  }

  releaseSession(sessionId?: string, generation?: number): void {
    const state = this.active;
    if (
      !state
      || (sessionId !== undefined && state.sessionId !== sessionId)
      || (generation !== undefined && state.generation !== generation)
    ) return;
    this.active = null;
    state.keyed = false;
    this.dropAllTransceivers(state);
    for (const deferred of state.pending.values()) {
      clearTimeout(deferred.timeout);
      deferred.reject(new Error('media_e2ee_session_released'));
    }
    state.pending.clear();
    state.worker.onmessage = null;
    state.worker.onerror = null;
    state.worker.onmessageerror = null;
    state.worker.terminate();
  }

  senderForSlot(sessionId: string, slot: PublicPairMediaSlot): RTCRtpSender {
    const state = this.requireState(sessionId);
    const value = state.slots.get(slot);
    if (!value) throw new Error('public_media_slot_invalid');
    return value.transceiver.sender;
  }

  slotForReceiver(sessionId: string, receiver: RTCRtpReceiver): PublicPairMediaSlot | null {
    const state = this.active;
    return state?.sessionId === sessionId ? state.receiverSlots.get(receiver) ?? null : null;
  }

  slotForSender(sessionId: string, sender: RTCRtpSender): PublicPairMediaSlot | null {
    const state = this.active;
    if (!state || state.failed || state.sessionId !== sessionId) return null;
    for (const [slot, value] of state.slots) {
      if (value.transceiver.sender === sender) return slot;
    }
    return null;
  }

  validateFinalTopology(sessionId: string): void {
    const state = this.requireState(sessionId);
    if (state.peer.getTransceivers().length !== PUBLIC_PAIR_MEDIA_SLOTS.length) {
      throw new Error('public_media_topology_invalid');
    }
    const mids = new Set<string>();
    for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
      const transceiver = state.slots.get(definition.slot)?.transceiver;
      const mid = transceiver?.mid;
      if (
        !transceiver
        || !mid
        || mids.has(mid)
        || transceiver.direction !== 'sendrecv'
        || transceiver.currentDirection !== 'sendrecv'
      ) throw new Error('public_media_topology_invalid');
      mids.add(mid);
    }
  }

  private handleWorkerMessage(state: AdapterState, raw: unknown): void {
    if (this.active !== state || state.failed || !raw || typeof raw !== 'object' || Array.isArray(raw)) return;
    const message = raw as Record<string, unknown>;
    if (message['version'] !== 1 || typeof message['type'] !== 'string') {
      this.fail(state, 'media_e2ee_worker_message_invalid');
      return;
    }
    if (message['type'] === 'fatal') {
      this.fail(state, reasonCode(message['reasonCode'], 'media_e2ee_transform_failed'));
      return;
    }
    if (message['type'] === 'transform-ready' && typeof message['transformId'] === 'string') {
      this.resolve(state, `ready:${message['transformId']}`);
      return;
    }
    if (message['type'] === 'keys-installed' && message['sessionId'] === state.sessionId) {
      const expectedIds = [...state.slots.values()]
        .flatMap(slot => [slot.sendTransformId, slot.receiveTransformId]);
      if (!exactStrings(message['transformIds'], expectedIds)) {
        this.fail(state, 'media_e2ee_key_ack_invalid');
        return;
      }
      this.resolve(state, `installed:${state.sessionId}`);
      return;
    }
    if (message['type'] === 'publication-gate-set') {
      let gate: PublicPairMediaOutboundPublicationGate;
      try {
        if (
          !hasExactFields(message, ['version', 'type', 'sessionId', 'gate'])
          || message['sessionId'] !== state.sessionId
        ) {
          throw new Error('media_e2ee_publication_gate_ack_invalid');
        }
        gate = normalizePublicationGate(message['gate'], state.contractExpiresAtMs, false);
      } catch {
        this.fail(state, 'media_e2ee_publication_gate_ack_invalid');
        return;
      }
      const pendingKey = publicationGatePendingKey(state.sessionId, gate);
      if (!state.pending.has(pendingKey)) {
        this.fail(state, 'media_e2ee_publication_gate_ack_invalid');
        return;
      }
      this.resolve(state, pendingKey);
      return;
    }
    if (message['type'] !== 'keys-cleared') this.fail(state, 'media_e2ee_worker_message_invalid');
  }

  private attachSlot(
    state: AdapterState,
    definition: typeof PUBLIC_PAIR_MEDIA_SLOTS[number],
    transceiver: RTCRtpTransceiver,
  ): Promise<void> {
    if (this.active !== state || state.failed || state.slots.has(definition.slot)) {
      throw new Error('public_media_topology_invalid');
    }
    const sendTransformId = transformId(state.sessionId, definition.slot, 'send');
    const receiveTransformId = transformId(state.sessionId, definition.slot, 'receive');
    const sendReady = this.expect(state, `ready:${sendTransformId}`);
    const receiveReady = this.expect(state, `ready:${receiveTransformId}`);
    const slot = Object.freeze({
      slot: definition.slot, transceiver, sendTransformId, receiveTransformId,
    });
    state.slots.set(definition.slot, slot);
    state.receiverSlots.set(transceiver.receiver, definition.slot);
    transceiver.sender.transform = new RTCRtpScriptTransform(state.worker, {
      version: 1, transformId: sendTransformId, operation: 'encrypt',
    });
    transceiver.receiver.transform = new RTCRtpScriptTransform(state.worker, {
      version: 1, transformId: receiveTransformId, operation: 'decrypt',
    });
    setExactCodecPreference(transceiver, definition.kind, definition.codec);
    const readiness = Promise.all([sendReady, receiveReady]).then(() => {
      if (this.active !== state || state.failed) throw new Error('media_e2ee_session_released');
    });
    state.slotReadiness.set(definition.slot, readiness);
    return readiness;
  }

  private expect(state: AdapterState, key: string): Promise<void> {
    if (state.pending.has(key)) throw new Error('media_e2ee_worker_ack_duplicate');
    let resolve!: () => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<void>((accept, deny) => { resolve = accept; reject = deny; });
    const timeout = setTimeout(() => {
      if (!state.pending.delete(key)) return;
      reject(new Error('media_e2ee_worker_ack_timeout'));
      this.fail(state, 'media_e2ee_worker_ack_timeout');
    }, WORKER_ACK_TIMEOUT_MS);
    state.pending.set(key, { promise, resolve, reject, timeout });
    return promise;
  }

  private resolve(state: AdapterState, key: string): void {
    const deferred = state.pending.get(key);
    if (!deferred) {
      this.fail(state, 'media_e2ee_worker_ack_unexpected');
      return;
    }
    state.pending.delete(key);
    clearTimeout(deferred.timeout);
    deferred.resolve();
  }

  private requireState(sessionId: string, generation?: number): AdapterState {
    const state = this.active;
    if (!state || state.sessionId !== sessionId
        || (generation !== undefined && state.generation !== generation) || state.failed) {
      throw new Error('public_media_transform_not_prepared');
    }
    return state;
  }

  private fail(state: AdapterState, reasonCode: string): void {
    if (state.failed || this.active !== state) return;
    this.active = null;
    state.failed = true;
    state.keyed = false;
    this.dropAllTransceivers(state);
    for (const deferred of state.pending.values()) {
      clearTimeout(deferred.timeout);
      deferred.reject(new Error(reasonCode));
    }
    state.pending.clear();
    state.worker.onmessage = null;
    state.worker.onerror = null;
    state.worker.onmessageerror = null;
    state.worker.terminate();
    try { state.onFatal(reasonCode); } catch { /* Fatal cleanup remains complete. */ }
  }

  private dropAllTransceivers(state: AdapterState): void {
    for (const transceiver of state.peer.getTransceivers()) {
      try { transceiver.direction = 'inactive'; } catch { /* Closing peer already rejects media. */ }
      void transceiver.sender.replaceTrack(null).catch(() => undefined);
    }
  }
}

function setExactCodecPreference(
  transceiver: RTCRtpTransceiver,
  kind: 'audio' | 'video',
  codec: 'opus' | 'vp8',
): void {
  const capabilities = RTCRtpSender.getCapabilities(kind);
  const codecs = capabilities?.codecs.filter(
    candidate => candidate.mimeType.toLowerCase() === `${kind}/${codec}`,
  ) ?? [];
  if (codecs.length < 1) throw new Error(`public_media_${codec}_unsupported`);
  transceiver.setCodecPreferences(codecs);
}

function transformId(
  sessionId: string,
  slot: PublicPairMediaSlot,
  direction: 'send' | 'receive',
): string {
  const value = `${sessionId}:${slot}:${direction}`;
  if (value.length > 128) throw new Error('media_e2ee_transform_id_invalid');
  return value;
}

function validateSlotContext(
  context: PairMediaE2eeFrameContext,
  slot: PublicPairMediaSlot,
  kind: 'audio' | 'video',
  codec: 'opus' | 'vp8',
): void {
  if (context.slot !== slot || context.kind !== kind || context.codec !== codec) {
    throw new Error('media_e2ee_key_set_invalid');
  }
}

function normalizePublicationGate(
  raw: unknown,
  contractExpiresAtMs: number,
  requireLive: boolean,
): PublicPairMediaOutboundPublicationGate {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('public_media_publication_gate_invalid');
  }
  const value = raw as Record<string, unknown>;
  const fields = ['revision', 'enabled', 'slots', 'expiresAtMs'];
  const keys = Object.keys(value);
  const slots = value['slots'];
  const allowedSlots = new Set<string>(PUBLIC_PAIR_MEDIA_SLOTS.map(definition => definition.slot));
  if (
    keys.length !== fields.length
    || fields.some(field => !(field in value))
    || !Number.isSafeInteger(value['revision'])
    || (value['revision'] as number) < 1
    || typeof value['enabled'] !== 'boolean'
    || !Number.isSafeInteger(value['expiresAtMs'])
    || (value['expiresAtMs'] as number) < 0
    || (value['expiresAtMs'] as number) > contractExpiresAtMs
    || !Array.isArray(slots)
    || slots.length > PUBLIC_PAIR_MEDIA_SLOTS.length
    || new Set(slots).size !== slots.length
    || slots.some(slot => typeof slot !== 'string' || !allowedSlots.has(slot))
  ) throw new Error('public_media_publication_gate_invalid');
  const gate = Object.freeze({
    revision: value['revision'] as number,
    enabled: value['enabled'] as boolean,
    slots: Object.freeze([...(slots as PublicPairMediaSlot[])]),
    expiresAtMs: value['expiresAtMs'] as number,
  });
  if (requireLive) assertPublicationGateLive(gate);
  return gate;
}

function assertPublicationGateLive(gate: Readonly<PublicPairMediaOutboundPublicationGate>): void {
  if (gate.enabled && gate.expiresAtMs <= Date.now()) {
    throw new Error('public_media_publication_gate_expired');
  }
}

function publicationGatePendingKey(
  sessionId: string,
  gate: Readonly<PublicPairMediaOutboundPublicationGate>,
): string {
  return `publication-gate:${sessionId}:${JSON.stringify({
    revision: gate.revision,
    enabled: gate.enabled,
    slots: gate.slots,
    expiresAtMs: gate.expiresAtMs,
  })}`;
}

function hasExactFields(value: Readonly<Record<string, unknown>>, fields: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === fields.length && fields.every(field => field in value);
}

function deferredReason(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

function reason(error: unknown, fallback: string): string {
  const candidate = deferredReason(error);
  return /^[a-z][a-z0-9_]{2,119}$/.test(candidate) ? candidate : fallback;
}

function reasonCode(value: unknown, fallback: string): string {
  return typeof value === 'string' && /^[a-z][a-z0-9_]{2,119}$/.test(value) ? value : fallback;
}

function exactStrings(value: unknown, expected: readonly string[]): boolean {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}
