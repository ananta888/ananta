import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { PairMediaE2eeTransformAdapter, PublicPairMediaSlotKeySet } from './pair-media-e2ee-transform.adapter';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import {
  PUBLIC_PAIR_MEDIA_GRANTS,
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaSecurityContractV2,
} from './public-pair-media-security-contract';
import { PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 } from './pair-media-frame-format';
import {
  SEMANTIC_DC_VERSION,
  SemanticDataChannelMessage,
  validateSemanticDcMessage,
} from './webrtc-datachannel.service';
import { VerifiedPeerBinding, WebrtcPeerKeyService } from './webrtc-peer-key.service';
import {
  canonicalSecurityJson,
  decodeB64,
  encodeB64,
} from './webrtc-secure-envelope';

export type PublicPairMediaE2eeStateName =
  | 'inactive'
  | 'awaiting-security'
  | 'awaiting-peer'
  | 'negotiating'
  | 'ready'
  | 'failed';

export interface PublicPairMediaE2eeState {
  readonly sessionId: string;
  readonly state: PublicPairMediaE2eeStateName;
  readonly reasonCode?: string;
  readonly contractDigest?: string;
}

/** Lower-level port registered by WebrtcSessionService; no reverse DI edge. */
export interface PairMediaE2eeTransportPort {
  /** Generation-bound consent-channel readiness; never infer this from signaling. */
  isOpen(): boolean;
  send(message: SemanticDataChannelMessage): Promise<void>;
  disableMedia(reasonCode: string): void;
  failClosed(reasonCode: string): void;
}

interface MediaHelloV2 {
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

interface MediaHelloAckV2 {
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

interface Runtime {
  readonly sessionId: string;
  readonly generation: number;
  readonly adapterGeneration: number;
  readonly contract: Readonly<PublicPairMediaSecurityContractV2>;
  readonly binding: Readonly<VerifiedPeerBinding>;
  port: PairMediaE2eeTransportPort | null;
  dataChannelOpen: boolean;
  topologyReady: boolean;
  localActivated: boolean;
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

const ACTIVATION_WAIT_MS = 15_000;
const CONTROL_TTL_MS = 30_000;
const CONTROL_MESSAGE_PREFIX = 'pair-media-';
const DIGEST_RE = /^[a-f0-9]{64}$/;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;

@Injectable({ providedIn: 'root' })
export class PairMediaE2eeCoordinatorService {
  private readonly bootstrap = inject(PairViewSecurityBootstrapService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly encryption = inject(E2eEncryptionService);
  private readonly sequences = inject(PairSecureSequenceService);
  private readonly transforms = inject(PairMediaE2eeTransformAdapter);
  private readonly statusSubject = new BehaviorSubject<PublicPairMediaE2eeState>(
    Object.freeze({ sessionId: '', state: 'inactive' }),
  );
  readonly status$: Observable<PublicPairMediaE2eeState> = this.statusSubject.asObservable();
  private runtime: Runtime | null = null;
  private runtimeGeneration = 0;

  statusFor(sessionId: string): PublicPairMediaE2eeState {
    if (!sessionId) return Object.freeze({ sessionId: '', state: 'inactive' });
    const runtime = this.runtime;
    if (runtime?.sessionId === sessionId) {
      if (runtime.contract.expires_at_ms <= Date.now()) {
        this.failRuntime(runtime, 'public_media_contract_expired', true);
      }
      return this.statusSubject.value.sessionId === sessionId
        ? this.statusSubject.value
        : this.derivedStatus(sessionId);
    }
    return this.derivedStatus(sessionId);
  }

  canActivate(sessionId: string): boolean {
    const contract = this.bootstrap.mediaContractFor(sessionId);
    if (
      !contract
      || contract.version !== 2
      || contract.frame_format !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
      || contract.expires_at_ms <= Date.now()
    ) return false;
    const runtime = this.runtime;
    if (runtime?.sessionId === sessionId && !runtime.dataChannelOpen) {
      this.reconcileDataChannelOpen(runtime);
    }
    if (
      !runtime
      || runtime.sessionId !== sessionId
      || runtime.failed
      || runtime.installed
      || !runtime.topologyReady
      || !runtime.dataChannelOpen
    ) return false;
    return this.transforms.isPrepared(
      sessionId, contract.epoch, contract.digest, runtime.adapterGeneration,
    );
  }

  async activate(sessionId: string): Promise<PublicPairMediaE2eeState> {
    let runtime: Runtime;
    try {
      runtime = this.ensureRuntime(sessionId);
    } catch (error) {
      const state = Object.freeze({
        sessionId,
        state: 'failed' as const,
        reasonCode: reason(error, 'public_media_contract_not_ready'),
      });
      this.statusSubject.next(state);
      return state;
    }
    if (runtime.contract.expires_at_ms <= Date.now()) {
      this.failRuntime(runtime, 'public_media_contract_expired', true);
      return this.statusFor(sessionId);
    }
    if (runtime.installed) return this.statusFor(sessionId);
    try {
      this.refreshExpiredPreKeyHandshake(runtime);
    } catch (error) {
      this.failRuntime(runtime, reason(error, 'public_media_control_refresh_failed'), true);
      return this.statusFor(sessionId);
    }
    runtime.localActivated = true;
    this.emit(runtime, 'awaiting-peer', 'public_media_local_activation_pending');
    void this.maybeSendHello(runtime).catch(error => {
      this.failRuntime(runtime, reason(error, 'public_media_hello_send_failed'), true);
    });
    return this.waitForActivation(runtime);
  }

  deactivate(sessionId: string, reasonCode = 'public_media_deactivated'): void {
    const runtime = this.runtime;
    if (!runtime || runtime.sessionId !== sessionId) {
      this.statusSubject.next(Object.freeze({ sessionId, state: 'inactive', reasonCode }));
      return;
    }
    const port = runtime.port;
    this.clearExpiry(runtime);
    runtime.poisoned = true;
    runtime.port = null;
    runtime.dataChannelOpen = false;
    runtime.localActivated = false;
    runtime.failed = false;
    this.transforms.releaseSession(sessionId, runtime.adapterGeneration);
    this.runtime = null;
    this.statusSubject.next(Object.freeze({
      sessionId, state: 'inactive', reasonCode, contractDigest: runtime.contract.digest,
    }));
    try { port?.failClosed(reasonCode); } catch { /* Local teardown is already complete. */ }
  }

  bindTransport(sessionId: string, port: PairMediaE2eeTransportPort): void {
    const runtime = this.ensureRuntime(sessionId);
    runtime.port = port;
    if (runtime.localActivated) {
      void this.maybeSendHello(runtime).catch(error => {
        this.failRuntime(runtime, reason(error, 'public_media_hello_send_failed'), true);
      });
    }
  }

  unbindTransport(sessionId: string, reasonCode = 'public_media_transport_closed'): void {
    const runtime = this.runtime;
    if (!runtime || runtime.sessionId !== sessionId) return;
    this.clearExpiry(runtime);
    runtime.poisoned = true;
    runtime.dataChannelOpen = false;
    runtime.port = null;
    this.transforms.releaseSession(sessionId, runtime.adapterGeneration);
    this.runtime = null;
    this.statusSubject.next(Object.freeze({
      sessionId, state: 'inactive', reasonCode, contractDigest: runtime.contract.digest,
    }));
  }

  markDataChannelOpen(sessionId: string): void {
    const runtime = this.ensureRuntime(sessionId);
    runtime.dataChannelOpen = true;
    if (runtime.localActivated) {
      void this.maybeSendHello(runtime).catch(error => {
        this.failRuntime(runtime, reason(error, 'public_media_hello_send_failed'), true);
      });
    } else {
      this.emit(runtime, 'inactive');
    }
  }

  markTopologyNegotiated(sessionId: string): void {
    const runtime = this.ensureRuntime(sessionId);
    try {
      this.transforms.validateFinalTopology(sessionId);
      runtime.topologyReady = true;
      if (!runtime.localActivated) this.emit(runtime, 'inactive');
      void this.maybeInstall(runtime);
    } catch (error) {
      // A peer that could not advertise the optional media extension may
      // still have a valid authenticated DataChannel. No key was installed,
      // so dropping all transforms is sufficient to preserve data-only Pair.
      this.failRuntime(
        runtime,
        reason(error, 'public_media_topology_invalid'),
        true,
        runtime.peerMayBeReady || runtime.installAttempted || runtime.installed,
      );
    }
  }

  /** Returns true when the message belonged to the private media protocol. */
  async acceptSemantic(message: SemanticDataChannelMessage): Promise<boolean> {
    if (!message.message_id.startsWith(CONTROL_MESSAGE_PREFIX)) return false;
    const runtime = this.runtime;
    if (!runtime || runtime.sessionId !== message.session_id) return false;
    try {
      const control = await this.openControl(runtime, message);
      this.assertCurrent(runtime);
      if (control.kind === 'hello') await this.acceptHello(runtime, control);
      else await this.acceptAck(runtime, control);
    } catch (error) {
      this.failRuntime(runtime, reason(error, 'public_media_control_invalid'), true);
    }
    return true;
  }

  fail(sessionId: string, reasonCode: string): void {
    const runtime = this.runtime;
    if (runtime?.sessionId === sessionId) this.failRuntime(runtime, reasonCode, true);
    else this.statusSubject.next(Object.freeze({ sessionId, state: 'failed', reasonCode }));
  }

  /**
   * Fails only the optional media extension while it is still DROP-first.
   * Once media keys were released, the same failure is transport-fatal.
   */
  failMediaExtension(sessionId: string, reasonCode: string): void {
    const runtime = this.runtime;
    if (runtime?.sessionId === sessionId) {
      this.failRuntime(
        runtime, reasonCode, true,
        runtime.peerMayBeReady || runtime.installAttempted || runtime.installed,
      );
    } else {
      this.statusSubject.next(Object.freeze({ sessionId, state: 'failed', reasonCode }));
    }
  }

  private ensureRuntime(sessionId: string): Runtime {
    const contract = this.bootstrap.mediaContractFor(sessionId);
    const binding = this.peerKeys.requireBinding(true);
    const adapterGeneration = contract
      ? this.transforms.generationForSession(sessionId) : null;
    if (
      !contract
      || contract.version !== 2
      || contract.domain !== 'ananta.public-pair.media-security-contract.v2'
      || contract.frame_format !== PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2
      || contract.session_id !== sessionId
      || contract.epoch !== binding.epoch
      || contract.expires_at_ms <= Date.now()
      || binding.scopeId !== sessionId
      || adapterGeneration === null
      || !this.transforms.isPrepared(sessionId, contract.epoch, contract.digest, adapterGeneration)
    ) throw new Error('public_media_contract_not_ready');
    const current = this.runtime;
    if (
      current
      && current.sessionId === sessionId
      && current.contract.digest === contract.digest
      && current.contract.epoch === contract.epoch
      && current.binding.keyId === binding.keyId
      && current.adapterGeneration === adapterGeneration
      && !current.failed && !current.poisoned
    ) return current;
    if (current) this.unbindTransport(current.sessionId, 'public_media_security_context_changed');
    const runtime: Runtime = {
      sessionId,
      generation: ++this.runtimeGeneration,
      adapterGeneration,
      contract,
      binding,
      port: null,
      dataChannelOpen: false,
      topologyReady: false,
      localActivated: false,
      localHello: null,
      localHelloDigest: '',
      remoteHello: null,
      remoteHelloDigest: '',
      localAck: null,
      remoteAck: null,
      localAckSent: false,
      helloSendPromise: null,
      ackSendPromise: null,
      installPromise: null,
      installAttempted: false,
      peerMayBeReady: false,
      installed: false,
      failed: false,
      poisoned: false,
      expiryTimer: null,
    };
    this.runtime = runtime;
    this.armExpiry(runtime);
    this.emit(runtime, 'inactive');
    return runtime;
  }

  private async maybeSendHello(runtime: Runtime): Promise<void> {
    this.assertCurrent(runtime);
    this.refreshExpiredPreKeyHandshake(runtime);
    this.reconcileDataChannelOpen(runtime);
    if (
      runtime.failed || !runtime.localActivated || !runtime.dataChannelOpen || !runtime.port
      || runtime.helloSendPromise || runtime.localHello
    ) return runtime.helloSendPromise ?? Promise.resolve();
    runtime.helloSendPromise = (async () => {
      const expiresAtMs = Math.min(runtime.contract.expires_at_ms, Date.now() + CONTROL_TTL_MS);
      if (expiresAtMs <= Date.now()) throw new Error('public_media_contract_expired');
      const salt = crypto.getRandomValues(new Uint8Array(16));
      const hello = parseHello({
        schema: 'ananta.public-pair.media-hello.v2',
        kind: 'hello',
        session_id: runtime.sessionId,
        epoch: runtime.contract.epoch,
        sender_id: runtime.binding.localPeerId,
        recipient_id: runtime.binding.remotePeerId,
        media_contract_digest: runtime.contract.digest,
        frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
        connection_salt_b64: encodeB64(salt),
        slots: PUBLIC_PAIR_MEDIA_GRANTS,
        expires_at_ms: expiresAtMs,
      }, runtime, 'outbound');
      const helloDigest = await digestCanonical(hello);
      this.assertCurrent(runtime);
      // Publish the hello and its digest atomically before transport send. A
      // loopback/fast peer may answer before send() resolves.
      runtime.localHello = hello;
      runtime.localHelloDigest = helloDigest;
      this.emit(runtime, 'awaiting-peer', 'public_media_local_hello_preparing');
      await this.sendControl(runtime, hello);
      this.assertCurrent(runtime);
      if (!runtime.remoteHello) {
        this.emit(runtime, 'awaiting-peer', 'public_media_local_hello_sent');
      }
      await this.maybeSendAck(runtime);
    })().finally(() => { runtime.helloSendPromise = null; });
    return runtime.helloSendPromise;
  }

  private reconcileDataChannelOpen(runtime: Runtime): void {
    if (runtime.dataChannelOpen || !runtime.port || runtime.failed || runtime.poisoned) return;
    try {
      runtime.dataChannelOpen = runtime.port.isOpen() === true;
    } catch (error) {
      this.failRuntime(
        runtime,
        reason(error, 'public_media_consent_channel_state_failed'),
        true,
      );
    }
  }

  private async acceptHello(runtime: Runtime, hello: MediaHelloV2): Promise<void> {
    const digest = await digestCanonical(hello);
    this.assertCurrent(runtime);
    if (runtime.remoteHello && runtime.remoteHelloDigest !== digest) {
      if (
        runtime.remoteHello.expires_at_ms <= Date.now()
        && !runtime.peerMayBeReady
        && !runtime.installAttempted
        && !runtime.installed
        && !runtime.helloSendPromise
        && !runtime.ackSendPromise
      ) {
        runtime.remoteHello = null;
        runtime.remoteHelloDigest = '';
        this.resetAcks(runtime);
      } else {
        throw new Error('public_media_hello_conflict');
      }
    }
    runtime.remoteHello = hello;
    runtime.remoteHelloDigest = digest;
    this.emit(runtime, 'awaiting-peer', 'public_media_remote_hello_received');
    if (runtime.localActivated) {
      await this.maybeSendHello(runtime);
      await this.maybeSendAck(runtime);
    }
  }

  private async maybeSendAck(runtime: Runtime): Promise<void> {
    this.assertCurrent(runtime);
    if (
      runtime.failed || !runtime.localActivated || !runtime.localHello || !runtime.remoteHello
      || runtime.localAckSent || runtime.ackSendPromise
    ) return runtime.ackSendPromise ?? Promise.resolve();
    runtime.ackSendPromise = (async () => {
      const ack = parseAck({
        schema: 'ananta.public-pair.media-hello-ack.v2',
        kind: 'hello_ack',
        session_id: runtime.sessionId,
        epoch: runtime.contract.epoch,
        sender_id: runtime.binding.localPeerId,
        recipient_id: runtime.binding.remotePeerId,
        media_contract_digest: runtime.contract.digest,
        frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
        hello_digests: helloDigestRows(runtime),
        expires_at_ms: Math.min(runtime.contract.expires_at_ms, Date.now() + CONTROL_TTL_MS),
      }, runtime, 'outbound');
      // After this point send() may have delivered the ACK even if its Promise
      // is unresolved/rejected. The peer can derive and release media keys.
      runtime.peerMayBeReady = true;
      await this.sendControl(runtime, ack);
      this.assertCurrent(runtime);
      runtime.localAck = ack;
      runtime.localAckSent = true;
      if (!runtime.remoteAck) {
        this.emit(runtime, 'awaiting-peer', 'public_media_local_ack_sent');
      }
      await this.maybeInstall(runtime);
    })().finally(() => { runtime.ackSendPromise = null; });
    return runtime.ackSendPromise;
  }

  private async acceptAck(runtime: Runtime, ack: MediaHelloAckV2): Promise<void> {
    this.assertCurrent(runtime);
    if (!runtime.localHello || !runtime.remoteHello) throw new Error('public_media_hello_missing');
    if (canonicalSecurityJson(ack.hello_digests) !== canonicalSecurityJson(helloDigestRows(runtime))) {
      throw new Error('public_media_ack_binding_invalid');
    }
    if (runtime.remoteAck && canonicalSecurityJson(runtime.remoteAck) !== canonicalSecurityJson(ack)) {
      throw new Error('public_media_ack_conflict');
    }
    runtime.remoteAck = ack;
    this.emit(runtime, 'negotiating', 'public_media_remote_ack_received');
    await this.maybeInstall(runtime);
  }

  private async maybeInstall(runtime: Runtime): Promise<void> {
    this.assertCurrent(runtime);
    if (
      runtime.failed || runtime.installed || runtime.installPromise
      || !runtime.topologyReady || !runtime.localAckSent || !runtime.localAck || !runtime.remoteAck
      || !runtime.localHello || !runtime.remoteHello
    ) return runtime.installPromise ?? Promise.resolve();
    this.emit(runtime, 'negotiating');
    runtime.installPromise = (async () => {
      assertFreshHandshake(runtime);
      const connectionId = await connectionDigest(runtime);
      this.assertCurrent(runtime);
      const keys = await this.deriveSlotKeys(runtime, connectionId);
      this.assertCurrent(runtime);
      assertFreshHandshake(runtime);
      if (runtime.contract.expires_at_ms <= Date.now()) throw new Error('public_media_contract_expired');
      runtime.installAttempted = true;
      await this.transforms.installKeys(runtime.sessionId, keys, runtime.adapterGeneration);
      this.assertCurrent(runtime);
      runtime.installed = true;
      this.emit(runtime, 'ready');
    })().catch(error => {
      this.failRuntime(runtime, reason(error, 'media_e2ee_key_install_failed'), true);
      throw error;
    }).finally(() => { runtime.installPromise = null; });
    return runtime.installPromise;
  }

  private async deriveSlotKeys(
    runtime: Runtime,
    connectionId: string,
  ): Promise<readonly PublicPairMediaSlotKeySet[]> {
    const values: PublicPairMediaSlotKeySet[] = [];
    for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
      const sendBindingId = await frameKeyBindingDigest(
        runtime, connectionId, runtime.binding.localPeerId, runtime.binding.remotePeerId, definition.slot,
      );
      const receiveBindingId = await frameKeyBindingDigest(
        runtime, connectionId, runtime.binding.remotePeerId, runtime.binding.localPeerId, definition.slot,
      );
      const [sendKey, receiveKey] = await Promise.all([
        this.encryption.derivePurposeAesKey(runtime.binding, 'public-pair-media-frame-v2', sendBindingId),
        this.encryption.derivePurposeAesKey(runtime.binding, 'public-pair-media-frame-v2', receiveBindingId),
      ]);
      this.assertCurrent(runtime);
      values.push(Object.freeze({
        slot: definition.slot,
        sendKey,
        receiveKey,
        sendContext: Object.freeze({
          sessionId: runtime.sessionId,
          mediaContractDigest: runtime.contract.digest,
          connectionId,
          senderId: runtime.binding.localPeerId,
          recipientId: runtime.binding.remotePeerId,
          slot: definition.slot,
          codec: definition.codec,
          kind: definition.kind,
          keyEpoch: runtime.contract.epoch,
          contractExpiresAtMs: runtime.contract.expires_at_ms,
        }),
        receiveContext: Object.freeze({
          sessionId: runtime.sessionId,
          mediaContractDigest: runtime.contract.digest,
          connectionId,
          senderId: runtime.binding.remotePeerId,
          recipientId: runtime.binding.localPeerId,
          slot: definition.slot,
          codec: definition.codec,
          kind: definition.kind,
          keyEpoch: runtime.contract.epoch,
          contractExpiresAtMs: runtime.contract.expires_at_ms,
        }),
      }));
    }
    return Object.freeze(values);
  }

  private refreshExpiredPreKeyHandshake(runtime: Runtime): void {
    this.assertCurrent(runtime);
    const now = Date.now();
    const localExpired = runtime.localHello !== null && runtime.localHello.expires_at_ms <= now;
    const remoteExpired = runtime.remoteHello !== null && runtime.remoteHello.expires_at_ms <= now;
    if (!localExpired && !remoteExpired) return;
    if (
      runtime.peerMayBeReady
      || runtime.installAttempted
      || runtime.installed
      || runtime.helloSendPromise
      || runtime.ackSendPromise
    ) throw new Error('public_media_control_refresh_unsafe');
    if (localExpired) {
      runtime.localHello = null;
      runtime.localHelloDigest = '';
    }
    if (remoteExpired) {
      runtime.remoteHello = null;
      runtime.remoteHelloDigest = '';
    }
    this.resetAcks(runtime);
  }

  private resetAcks(runtime: Runtime): void {
    runtime.localAck = null;
    runtime.remoteAck = null;
    runtime.localAckSent = false;
  }

  private async sendControl(runtime: Runtime, control: MediaHelloV2 | MediaHelloAckV2): Promise<void> {
    this.assertCurrent(runtime);
    if (!runtime.port || !runtime.dataChannelOpen) throw new Error('public_media_transport_not_open');
    const sequence = await this.sequences.next(
      runtime.sessionId, runtime.contract.epoch, runtime.binding.localPeerId, 'media',
    );
    this.assertCurrent(runtime);
    this.emitControlStage(runtime, control, 'sequence_reserved');
    const envelope = await this.encryption.seal(
      runtime.binding,
      new TextEncoder().encode(canonicalSecurityJson(control)),
      {
        sequence,
        payloadType: 'public_pair_media_control',
        trafficClass: 'media',
        expiresAtMs: control.expires_at_ms,
      },
    );
    this.assertCurrent(runtime);
    this.emitControlStage(runtime, control, 'sealed');
    const envelopeBytes = new TextEncoder().encode(canonicalSecurityJson(envelope));
    const message = await validateSemanticDcMessage({
      version: SEMANTIC_DC_VERSION,
      traffic_class: 'control',
      message_id: `${CONTROL_MESSAGE_PREFIX}${control.kind}-${runtime.contract.epoch}-${sequence}`,
      session_id: runtime.sessionId,
      epoch: runtime.contract.epoch,
      sender_id: runtime.binding.localPeerId,
      audience_id: runtime.binding.remotePeerId,
      sequence,
      expires_at_ms: control.expires_at_ms,
      compression: 'none',
      security: { algorithm: 'AES-GCM-256', key_id: runtime.binding.keyId },
      payload_bytes: envelopeBytes.byteLength,
      payload_digest: await digestBytes(envelopeBytes),
      ciphertext: encodeB64(envelopeBytes),
    });
    this.assertCurrent(runtime);
    this.emitControlStage(runtime, control, 'framed');
    await runtime.port.send(message);
    this.assertCurrent(runtime);
    this.emitControlStage(runtime, control, 'queued');
  }

  private emitControlStage(
    runtime: Runtime,
    control: MediaHelloV2 | MediaHelloAckV2,
    stage: 'sequence_reserved' | 'sealed' | 'framed' | 'queued',
  ): void {
    const kind = control.kind === 'hello_ack' ? 'ack' : 'hello';
    this.emit(runtime, 'awaiting-peer', `public_media_local_${kind}_${stage}`);
  }

  private async openControl(
    runtime: Runtime,
    raw: SemanticDataChannelMessage,
  ): Promise<MediaHelloV2 | MediaHelloAckV2> {
    const message = await validateSemanticDcMessage(raw);
    this.assertCurrent(runtime);
    if (
      message.session_id !== runtime.sessionId
      || message.epoch !== runtime.contract.epoch
      || message.sender_id !== runtime.binding.remotePeerId
      || message.audience_id !== runtime.binding.localPeerId
      || message.security.key_id !== runtime.binding.keyId
      || message.expires_at_ms <= Date.now()
      || message.expires_at_ms > runtime.contract.expires_at_ms
    ) throw new Error('public_media_control_context_mismatch');
    let envelope: unknown;
    try {
      envelope = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decodeB64(message.ciphertext)));
    } catch {
      throw new Error('public_media_control_ciphertext_invalid');
    }
    const opened = await this.encryption.open(runtime.binding, envelope);
    this.assertCurrent(runtime);
    if (
      opened.envelope.payload_type !== 'public_pair_media_control'
      || opened.envelope.aad.traffic_class !== 'media'
      || opened.envelope.sequence !== message.sequence
      || opened.envelope.expires_at_ms !== message.expires_at_ms
    ) throw new Error('public_media_control_envelope_mismatch');
    let control: unknown;
    try {
      control = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(opened.plaintext));
    } catch {
      throw new Error('public_media_control_plaintext_invalid');
    }
    const kind = (control as Record<string, unknown> | null)?.['kind'];
    return kind === 'hello'
      ? parseHello(control, runtime, 'inbound')
      : parseAck(control, runtime, 'inbound');
  }

  private waitForActivation(runtime: Runtime): Promise<PublicPairMediaE2eeState> {
    const current = this.statusFor(runtime.sessionId);
    if (current.state === 'ready' || current.state === 'failed') return Promise.resolve(current);
    return new Promise(resolve => {
      let settled = false;
      let timeout: ReturnType<typeof setTimeout> | null = null;
      const subscription = this.status$.subscribe(state => {
        if (settled || state.sessionId !== runtime.sessionId) return;
        if (this.runtime === runtime && state.state === 'failed') {
          settled = true;
          if (timeout !== null) clearTimeout(timeout);
          subscription.unsubscribe();
          resolve(state);
          return;
        }
        if (this.runtime !== runtime || runtime.poisoned) {
          settled = true;
          if (timeout !== null) clearTimeout(timeout);
          subscription.unsubscribe();
          resolve(state.state === 'inactive' ? state : Object.freeze({
            sessionId: runtime.sessionId,
            state: 'inactive',
            reasonCode: 'public_media_activation_superseded',
            contractDigest: runtime.contract.digest,
          }));
          return;
        }
        if (state.state === 'ready') {
          settled = true;
          if (timeout !== null) clearTimeout(timeout);
          subscription.unsubscribe();
          resolve(state);
        }
      });
      timeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        subscription.unsubscribe();
        if (this.runtime !== runtime || runtime.poisoned) {
          resolve(Object.freeze({
            sessionId: runtime.sessionId,
            state: 'failed',
            reasonCode: 'public_media_activation_superseded',
            contractDigest: runtime.contract.digest,
          }));
          return;
        }
        const state = Object.freeze({
          ...this.statusFor(runtime.sessionId),
          reasonCode: 'public_media_peer_activation_pending',
        });
        this.statusSubject.next(state);
        resolve(state);
      }, ACTIVATION_WAIT_MS);
    });
  }

  private derivedStatus(sessionId: string): PublicPairMediaE2eeState {
    const contract = this.bootstrap.mediaContractFor(sessionId);
    if (!contract) return Object.freeze({
      sessionId, state: 'awaiting-security', reasonCode: 'public_media_contract_missing',
    });
    if (contract.expires_at_ms <= Date.now()) return Object.freeze({
      sessionId, state: 'failed', reasonCode: 'public_media_contract_expired', contractDigest: contract.digest,
    });
    if (!this.transforms.isPrepared(sessionId, contract.epoch, contract.digest)) return Object.freeze({
      sessionId, state: 'awaiting-peer', reasonCode: 'public_media_transform_not_prepared',
      contractDigest: contract.digest,
    });
    return Object.freeze({ sessionId, state: 'inactive', contractDigest: contract.digest });
  }

  private emit(runtime: Runtime, state: PublicPairMediaE2eeStateName, reasonCode?: string): void {
    if (this.runtime !== runtime) return;
    this.statusSubject.next(Object.freeze({
      sessionId: runtime.sessionId,
      state,
      ...(reasonCode ? { reasonCode } : {}),
      contractDigest: runtime.contract.digest,
    }));
  }

  private failRuntime(
    runtime: Runtime,
    reasonCode: string,
    releaseWorker: boolean,
    terminateTransport = true,
  ): void {
    if (runtime.failed || runtime.poisoned || this.runtime !== runtime) return;
    const port = runtime.port;
    runtime.failed = true;
    runtime.poisoned = true;
    runtime.port = null;
    runtime.dataChannelOpen = false;
    runtime.localActivated = false;
    this.clearExpiry(runtime);
    if (releaseWorker) this.transforms.releaseSession(runtime.sessionId, runtime.adapterGeneration);
    if (!terminateTransport) {
      try { port?.disableMedia(reasonCode); } catch { /* Media is already locally disabled. */ }
    }
    this.emit(runtime, 'failed', reasonCode);
    if (terminateTransport) {
      try { port?.failClosed(reasonCode); } catch { /* Fail-closed state remains authoritative. */ }
    }
  }

  private armExpiry(runtime: Runtime): void {
    this.clearExpiry(runtime);
    const delay = Math.max(1, Math.min(2_147_483_647, runtime.contract.expires_at_ms - Date.now()));
    runtime.expiryTimer = setTimeout(() => {
      this.failRuntime(runtime, 'public_media_contract_expired', true);
    }, delay);
  }

  private clearExpiry(runtime: Runtime): void {
    if (runtime.expiryTimer !== null) clearTimeout(runtime.expiryTimer);
    runtime.expiryTimer = null;
  }

  private assertCurrent(runtime: Runtime): void {
    if (
      this.runtime !== runtime
      || runtime.generation !== this.runtimeGeneration
      || runtime.failed
      || runtime.poisoned
      || runtime.contract.expires_at_ms <= Date.now()
      || !this.transforms.isPrepared(
        runtime.sessionId,
        runtime.contract.epoch,
        runtime.contract.digest,
        runtime.adapterGeneration,
      )
    ) throw new Error('public_media_runtime_superseded');
  }
}

function parseHello(
  raw: unknown,
  runtime: Runtime,
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

function parseAck(
  raw: unknown,
  runtime: Runtime,
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

function validateDirectedPeers(
  value: Record<string, unknown>,
  runtime: Runtime,
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

function helloDigestRows(runtime: Runtime): MediaHelloAckV2['hello_digests'] {
  if (!runtime.localHelloDigest || !runtime.remoteHelloDigest) throw new Error('public_media_hello_missing');
  const rows = [
    Object.freeze({ peer_id: runtime.binding.localPeerId, digest: runtime.localHelloDigest }),
    Object.freeze({ peer_id: runtime.binding.remotePeerId, digest: runtime.remoteHelloDigest }),
  ].sort((left, right) => left.peer_id.localeCompare(right.peer_id));
  return Object.freeze(rows) as unknown as MediaHelloAckV2['hello_digests'];
}

function assertFreshHandshake(runtime: Runtime): void {
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

async function connectionDigest(runtime: Runtime): Promise<string> {
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

function frameKeyBindingDigest(
  runtime: Runtime,
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

function validControlExpiry(value: unknown, contractExpiry: number): boolean {
  return Number.isSafeInteger(value) && (value as number) > Date.now() && (value as number) <= contractExpiry;
}

function closedObject(raw: unknown, fields: readonly string[], reasonCode: string): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(reasonCode);
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(field => !(field in value))) throw new Error(reasonCode);
  return value;
}

function exactStrings(value: unknown, expected: readonly string[]): boolean {
  return Array.isArray(value)
    && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message)
    ? error.message : fallback;
}

function digestCanonical(value: unknown): Promise<string> {
  return digestBytes(new TextEncoder().encode(canonicalSecurityJson(value)));
}

async function digestBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
