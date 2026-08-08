import {
  buildPeerEvidenceAcceptancePayload,
  peerEvidenceAcceptEnabled,
  verifyPeerEvidenceOfferPreview,
} from '../features/voice/peer-evidence-acceptance';
import type { PeerEvidenceOfferView } from '../features/voice/peer-evidence-sync-panel.component';
import { SpeechEvidenceHubCurationFacade } from '../services/speech-evidence-hub-curation.facade';
import {
  SpeechEvidenceHubCurationResponse,
  SpeechEvidenceOfferRecord,
  SpeechEvidenceSyncApiService,
} from '../services/speech-evidence-sync-api.service';
import {
  SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
  SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION,
  SPEECH_EVIDENCE_PROTOCOL_VERSION,
  SpeechEvidenceMessage,
  SpeechEvidenceMessageType,
  canonicalSigningJson,
  sha256Canonical,
  speechEvidenceComparisonDigest,
  speechEvidenceGroupId,
  speechEvidenceQualityPolicyDigest,
  speechEvidenceResolutionDigest,
  speechEvidenceSpeakerScopeDigest,
  validateSpeechEvidenceMessage,
} from '../services/speech-evidence-sync.validators';
import { SemanticDataChannelMessage } from '../services/webrtc-datachannel.service';
import { WebrtcTransportService } from '../services/webrtc-transport.service';
import { WebrtcSignalingService } from '../services/webrtc-signaling.service';
import { PairSessionControlPlaneService } from '../services/pair-session-control-plane.service';
import { SemanticSpeechQualityControllerService } from '../services/semantic-speech-quality-controller.service';
import { VoiceApiService } from '../features/voice/voice-api.service';
import { firstValueFrom } from 'rxjs';

export interface SemanticPeerHubFixture {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly recipientId: string;
  readonly senderConsentDigest: string;
  readonly recipientConsentDigest: string;
  readonly scopeDigest: string;
  readonly consentVersion: number;
  readonly expiresAtMs: number;
  readonly transport: 'hub_relay' | 'webrtc';
}

export interface SemanticPeerHubOffer {
  readonly offerId: string;
  readonly inventoryRootDigest: string;
  readonly groupId: string;
  readonly groupB64: string;
  readonly totalBytes: number;
  readonly expiresAtMs: number;
  readonly value: Readonly<Record<string, unknown>>;
  readonly signedMessage: SpeechEvidenceMessage;
  readonly record: SpeechEvidenceOfferRecord;
}

export interface SemanticPeerHubAcceptanceResult {
  readonly signedPreviewVisible: boolean;
  readonly comparisonPreviewVisible: boolean;
  readonly productAcceptPolicyUsed: boolean;
  readonly productUiProjectionVisible: boolean;
}

export interface SemanticPeerHubCurationResult {
  readonly receiptVerified: boolean;
  readonly state: string;
  readonly acceptedGroupCount: number;
  readonly curationTaskQueued: boolean;
  readonly datasetReserved: boolean;
}

export interface SemanticPeerHubTransferExerciseResult {
  readonly backendRouteCount: number;
  readonly forcedRelayUsed: boolean;
  readonly duplicateRejected: boolean;
  readonly reorderRecovered: boolean;
}

export interface SemanticVoiceObservationCursor {
  readonly hubUrl: string;
  readonly profileId: string;
  readonly streamId: string;
  readonly liveRunId: string;
  readonly partialLatencyMs: number;
  readonly partialObserved: boolean;
}

export interface SemanticVoiceObservationResult {
  readonly partialLatencyMs: number;
  readonly partialObservationCount: number;
  readonly segmentModeCount: number;
  readonly finalSegmentCount: number;
  readonly correctionAfterFinalCount: number;
  readonly acceleratedRotationCount: number;
  readonly reconnectCount: number;
  readonly stream404Count: number;
  readonly stop409Count: number;
  readonly chunk413Count: number;
  readonly backpressureCount: number;
  readonly ordinaryFallbackCount: number;
  readonly transcriptContinuityCount: number;
}

export interface SemanticMediaPairHubProductDriver {
  prepare(fixture: SemanticPeerHubFixture): Promise<void>;
  propose(fixture: SemanticPeerHubFixture, relayCanary: string): Promise<SemanticPeerHubOffer>;
  accept(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubAcceptanceResult>;
  transfer(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<void>;
  exerciseTransfer(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubTransferExerciseResult>;
  recover(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  revoke(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<boolean>;
  acknowledge(fixture: SemanticPeerHubFixture, offer: SemanticPeerHubOffer): Promise<void>;
  curate(
    fixture: SemanticPeerHubFixture,
    offer: SemanticPeerHubOffer,
  ): Promise<SemanticPeerHubCurationResult>;
  openDirect(fixture: SemanticPeerHubFixture, initiator: boolean): Promise<'webrtc'>;
  sendDirect(fixture: SemanticPeerHubFixture, directCanary: string): Promise<string>;
  receiveDirect(messageId: string): Promise<boolean>;
  closeDirect(): Promise<void>;
  startVoiceObservation(hubUrl: string): Promise<SemanticVoiceObservationCursor>;
  resumeVoiceObservation(cursor: SemanticVoiceObservationCursor): Promise<SemanticVoiceObservationResult>;
}

export interface SemanticMediaPairProductPorts {
  readonly syncApi: SpeechEvidenceSyncApiService;
  readonly curation: SpeechEvidenceHubCurationFacade;
  readonly transport: WebrtcTransportService;
  readonly signaling: WebrtcSignalingService;
  /**
   * The live driver may only reuse sessions that the product control plane
   * has explicitly bound. Optional keeps stale E2E bootstraps fail-closed with
   * a useful setup error instead of silently selecting Hub relay signaling.
   */
  readonly controlPlane?: Pick<PairSessionControlPlaneService, 'assertSessionAvailable'>;
  readonly voiceApi: VoiceApiService;
  readonly speechQuality: SemanticSpeechQualityControllerService;
  readonly renderOfferPreview: (offer: PeerEvidenceOfferView) => boolean;
}

const E2E_QUERY = 'semanticMediaLiveE2e';
const PEER_CURATION_REQUEST_POLICY_DIGEST = 'bd02f0ea6843e13b4be73b3742f1d196a054522ffa4377ce0ddc339d39c46c19';
let productPorts: SemanticMediaPairProductPorts | null = null;
let hubSigningState: { keys: CryptoKeyPair; keyId: string; sequence: number } | null = null;
const directProductMessages = new Set<string>();
let directProductSubscription: { unsubscribe(): void } | null = null;

/** Install the adapter from the dedicated semantic-media E2E bootstrap only. */
export function installSemanticMediaPairLiveDriver(ports?: SemanticMediaPairProductPorts): void {
  if (ports) {
    productPorts = ports;
    directProductSubscription?.unsubscribe();
    directProductSubscription = ports.transport.semanticMessage$.subscribe(message => {
      directProductMessages.add(message.message_id);
    });
  }
  if (typeof window === 'undefined') return;
  const enabled = new URL(window.location.href).searchParams.get(E2E_QUERY) === '1';
  if (!enabled) return;
  const target = window as unknown as {
    __ANANTA_SEMANTIC_MEDIA_E2E__?: {
      pair: {
        hub: SemanticMediaPairHubProductDriver;
      };
    };
  };
  target.__ANANTA_SEMANTIC_MEDIA_E2E__ = Object.freeze({
    pair: Object.freeze({
      hub: Object.freeze({
        prepare: (fixture) => prepareHubPeer(fixture),
        propose: (fixture, canary) => proposeHubEvidence(fixture, canary),
        accept: (fixture, offer) => acceptHubEvidence(fixture, offer),
        transfer: (fixture, offer) => transferHubEvidence(fixture, offer),
        exerciseTransfer: (fixture, offer) => exerciseHubEvidenceTransfer(fixture, offer),
        recover: (fixture, offer) => recoverHubEvidence(fixture, offer),
        revoke: (fixture, offer) => revokeHubEvidence(fixture, offer),
        acknowledge: (fixture, offer) => acknowledgeHubEvidence(fixture, offer),
        curate: (fixture, offer) => curateHubEvidence(fixture, offer),
        openDirect: (fixture, initiator) => openDirectProductPath(fixture, initiator),
        sendDirect: (fixture, canary) => sendDirectProductPath(fixture, canary),
        receiveDirect: (messageId) => receiveDirectProductPath(messageId),
        closeDirect: () => closeDirectProductPath(),
        startVoiceObservation: (hubUrl) => startVoiceProductObservation(hubUrl),
        resumeVoiceObservation: (cursor) => resumeVoiceProductObservation(cursor),
      }),
    }),
  });
}

export async function openDirectProductPath(
  fixture: SemanticPeerHubFixture,
  initiator: boolean,
): Promise<'webrtc'> {
  validateHubFixture(fixture);
  if (fixture.transport !== 'webrtc') throw new Error('semantic_peer_direct_transport_required');
  directProductMessages.clear();
  const ports = requireProductPorts();
  const transport = ports.transport;
  if (!ports.controlPlane) throw new Error('semantic_peer_direct_pair_binding_setup_required');
  try {
    ports.controlPlane.assertSessionAvailable(fixture.sessionId);
  } catch (error) {
    throw new Error('semantic_peer_direct_pair_binding_setup_required', { cause: error });
  }
  await transport.open(fixture.sessionId, initiator, {
    semanticEpoch: fixture.epoch,
    semanticTrafficClasses: ['evidence_bulk', 'transcript'],
    remotePeerId: localAudience(fixture),
  });
  if (transport.mode$.value !== 'webrtc') throw new Error('semantic_peer_direct_product_path_unavailable');
  return 'webrtc';
}

async function sendDirectProductPath(
  fixture: SemanticPeerHubFixture,
  directCanary: string,
): Promise<string> {
  const ports = requireProductPorts();
  if (
    fixture.transport !== 'webrtc'
    || localSubject(fixture) !== fixture.senderId
    || !/^ANANTA_DIRECT_CANARY_[A-Za-z0-9]{24,96}$/.test(directCanary)
  ) throw new Error('semantic_peer_direct_context_invalid');
  const clear = new TextEncoder().encode(directCanary);
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const sealed = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: asArrayBuffer(nonce) }, key, asArrayBuffer(clear),
  ));
  const messageId = `semantic-e2e-direct-${crypto.randomUUID()}`;
  try {
    const message: SemanticDataChannelMessage = Object.freeze({
      version: 'ananta.webrtc-datachannel.v1',
      traffic_class: 'evidence_bulk',
      message_id: messageId,
      session_id: fixture.sessionId,
      epoch: fixture.epoch,
      sender_id: fixture.senderId,
      audience_id: fixture.recipientId,
      sequence: 1,
      expires_at_ms: boundedExpiry(fixture),
      compression: 'none',
      security: Object.freeze({ algorithm: 'AES-GCM-256', key_id: 'semantic-e2e-direct-opaque' }),
      payload_bytes: sealed.byteLength,
      payload_digest: await sha256Bytes(sealed),
      ciphertext: encodeB64(sealed),
    });
    await ports.transport.sendSemantic(message, { deadlineMs: Date.now() + 15_000 });
    return messageId;
  } finally {
    clear.fill(0);
    nonce.fill(0);
    sealed.fill(0);
  }
}

async function receiveDirectProductPath(messageId: string): Promise<boolean> {
  await waitUntil(() => directProductMessages.has(messageId), 15_000, 'semantic_peer_direct_delivery_timeout');
  return directProductMessages.has(messageId);
}

async function closeDirectProductPath(): Promise<void> {
  requireProductPorts().transport.close();
  directProductMessages.clear();
}

async function startVoiceProductObservation(hubUrl: string): Promise<SemanticVoiceObservationCursor> {
  if (!/^https?:\/\/[^\s]+$/.test(hubUrl)) throw new Error('semantic_voice_hub_url_invalid');
  const api = requireProductPorts().voiceApi;
  const suffix = crypto.randomUUID();
  const profileId = `semantic-voice-${suffix}`;
  await firstValueFrom(api.saveConfiguration(hubUrl, {
    scope: 'profile',
    scope_id: profileId,
    delta: {
      // The live gate must complete even when the checked-in local model
      // cannot meet incremental latency on the current CPU. The stream still
      // traverses the product API; absence of a native partial is reported as
      // zero and keeps the release gate closed instead of being simulated.
      transport_mode: 'batch',
      recognition_strategy: 'single',
      routing_strategy: 'fixed',
      primary_backend: 'whisper_cpp',
      secondary_backends: [],
      correction_policy: 'generative_rewrite',
      generative_corrector_provider: 'embedded',
      generative_corrector_model: 'phi-3-mini-instruct',
      generative_corrector_max_edit_ratio: 0.35,
      feature_flags: { generative_corrector: true },
    },
  }, `semantic-e2e-voice-config-${suffix}`));
  const stream = await firstValueFrom(api.createStream(hubUrl, {
    filename: 'semantic-e2e-reconnect.pcm',
    media_type: 'audio/pcm;rate=16000;channels=1',
    profile_id: profileId,
    language: 'de',
    max_audio_seconds: 2,
  }, `semantic-e2e-voice-stream-${suffix}`));
  const partialStarted = performance.now();
  const firstChunk = await firstValueFrom(api.pushStreamChunk(
    hubUrl,
    stream.stream.session_id,
    0,
    asArrayBuffer(syntheticPcm(100)),
  ));
  const partialLatencyMs = Math.max(1, Math.round(performance.now() - partialStarted));
  const partialObserved = firstChunk.event?.event_type === 'partial'
    && typeof firstChunk.event.payload?.text === 'string';
  const lease = await firstValueFrom(api.acquireLongRunLease(hubUrl, profileId));
  const liveRun = await firstValueFrom(api.createLongRun(hubUrl, {
    source: 'system_audio',
    profile_id: profileId,
    language: 'de',
    segment_duration_seconds: 60,
    max_duration_seconds: 120,
    overlap_milliseconds: 0,
    lease_token: lease.lease_token,
  }, `semantic-e2e-live-run-${suffix}`));
  if (liveRun.run.status !== 'active') throw new Error('semantic_voice_live_run_not_active');
  return Object.freeze({
    hubUrl,
    profileId,
    streamId: stream.stream.session_id,
    liveRunId: liveRun.run.id,
    partialLatencyMs: partialObserved ? partialLatencyMs : 0,
    partialObserved,
  });
}

async function resumeVoiceProductObservation(
  cursor: SemanticVoiceObservationCursor,
): Promise<SemanticVoiceObservationResult> {
  validateVoiceObservationCursor(cursor);
  const ports = requireProductPorts();
  const api = ports.voiceApi;
  let stream404Count = 0;
  let chunk413Count = 0;
  let stop409Count = 0;
  let backpressureCount = 0;
  let ordinaryFallbackCount = 0;
  let partialObservationCount = Number(cursor.partialObserved);
  let partialLatencyMs = cursor.partialLatencyMs;

  const resumedAt = performance.now();
  const resumedChunk = await firstValueFrom(api.pushStreamChunk(
    cursor.hubUrl,
    cursor.streamId,
    1,
    asArrayBuffer(syntheticPcm(100, 7)),
  ));
  if (
    resumedChunk.event?.event_type === 'partial'
    && typeof resumedChunk.event.payload?.text === 'string'
  ) {
    partialObservationCount += 1;
    partialLatencyMs = Math.max(partialLatencyMs, Math.max(1, Math.round(performance.now() - resumedAt)));
  }
  const final = await firstValueFrom(api.finalizeStream(cursor.hubUrl, cursor.streamId));
  const streamFinal = final.stream.state === 'final'
    && final.event?.event_type === 'final';
  const transcriptContinuityCount = Number(streamFinal && Boolean(final.result?.text));
  const reconnectCount = Number(streamFinal && resumedChunk.stream.next_chunk_sequence >= 2);

  try {
    await firstValueFrom(api.cancelStream(
      cursor.hubUrl,
      `voice-stream-missing-${crypto.randomUUID().replaceAll('-', '')}`,
      { missingSessionIsExpected: true },
    ));
  } catch (error) {
    stream404Count = Number(observedHttpStatus(error) === 404);
  }

  const oversized = await firstValueFrom(api.createStream(cursor.hubUrl, {
    filename: 'semantic-e2e-oversized.pcm',
    media_type: 'audio/pcm;rate=16000;channels=1',
    profile_id: cursor.profileId,
    max_audio_seconds: 2,
  }, `semantic-e2e-oversized-${crypto.randomUUID()}`));
  try {
    await firstValueFrom(api.pushStreamChunk(
      cursor.hubUrl,
      oversized.stream.session_id,
      0,
      asArrayBuffer(new Uint8Array(1024 * 1024 + 1)),
    ));
  } catch (error) {
    chunk413Count = Number(observedHttpStatus(error) === 413);
  } finally {
    await firstValueFrom(api.cancelStream(cursor.hubUrl, oversized.stream.session_id)).catch(() => undefined);
  }

  backpressureCount = await observeVoiceBackpressure(api, cursor.hubUrl, cursor.profileId);

  const rotationStarted = performance.now();
  const segmentResponses = [];
  for (let sequence = 0; sequence < 2; sequence += 1) {
    segmentResponses.push(await firstValueFrom(api.uploadLongRunSegment(
      cursor.hubUrl,
      cursor.liveRunId,
      sequence,
      {
        file: syntheticWav(120, sequence + 1),
        fileName: `semantic-e2e-segment-${sequence}.wav`,
        startedAtMs: sequence * 120,
        endedAtMs: (sequence + 1) * 120,
        durationMs: 120,
        overlapMilliseconds: 0,
      },
      `semantic-e2e-live-segment-${cursor.liveRunId}-${sequence}`,
    )));
  }
  const acceleratedRotationCount = Number(
    segmentResponses.length === 2
    && segmentResponses.every((value, index) => value.segment?.sequence === index)
    && performance.now() - rotationStarted < 60_000,
  );

  let stopped = false;
  try {
    await firstValueFrom(api.stopLongRun(
      cursor.hubUrl,
      cursor.liveRunId,
      { last_sequence: 1, reason: 'semantic_e2e_observation' },
      `semantic-e2e-live-stop-${cursor.liveRunId}`,
    ));
    stopped = true;
  } catch (error) {
    stop409Count = Number(observedHttpStatus(error) === 409);
  }
  let correctionSnapshot = await firstValueFrom(api.getLongRun(cursor.hubUrl, cursor.liveRunId));
  if (!stopped && stop409Count) {
    for (let attempt = 0; attempt < 40 && hasPendingCorrections(correctionSnapshot); attempt += 1) {
      await delay(250);
      correctionSnapshot = await firstValueFrom(api.getLongRun(cursor.hubUrl, cursor.liveRunId));
    }
    if (!hasPendingCorrections(correctionSnapshot)) {
      await firstValueFrom(api.stopLongRun(
        cursor.hubUrl,
        cursor.liveRunId,
        { last_sequence: 1, reason: 'semantic_e2e_observation' },
        `semantic-e2e-live-stop-complete-${cursor.liveRunId}`,
      ));
    }
  }
  const correctionAfterFinalCount = (correctionSnapshot.segments ?? []).filter(segment => (
    segment.status === 'completed'
    && !['', 'not_requested'].includes(String(segment.correction_status || ''))
  )).length;
  const finalSegmentCount = (correctionSnapshot.segments ?? []).filter(segment => (
    segment.status === 'completed'
  )).length;

  if (chunk413Count && streamFinal) {
    const fallback = ports.speechQuality.containRuntimeFailure('voice_stream.invalid_chunk');
    ordinaryFallbackCount = Number(fallback.mode === 'ordinary_audio' && fallback.ordinaryAudioAvailable);
  }
  await firstValueFrom(api.cancelStream(cursor.hubUrl, cursor.streamId)).catch(() => undefined);
  return Object.freeze({
    partialLatencyMs,
    partialObservationCount,
    segmentModeCount: Number(streamFinal && partialObservationCount === 0),
    finalSegmentCount,
    correctionAfterFinalCount,
    acceleratedRotationCount,
    reconnectCount,
    stream404Count,
    stop409Count,
    chunk413Count,
    backpressureCount,
    ordinaryFallbackCount,
    transcriptContinuityCount,
  });
}

async function observeVoiceBackpressure(
  api: VoiceApiService,
  hubUrl: string,
  profileId: string,
): Promise<number> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const marker = crypto.randomUUID();
    const created = await firstValueFrom(api.createStream(hubUrl, {
      filename: `semantic-e2e-pressure-${attempt}.pcm`,
      media_type: 'audio/pcm;rate=16000;channels=1',
      profile_id: profileId,
      max_audio_seconds: 40,
    }, `semantic-e2e-pressure-${marker}`));
    const bytes = asArrayBuffer(syntheticPcm(30_000, attempt + 3));
    const outcomes = await Promise.allSettled([
      firstValueFrom(api.pushStreamChunk(hubUrl, created.stream.session_id, 0, bytes)),
      firstValueFrom(api.pushStreamChunk(hubUrl, created.stream.session_id, 0, bytes)),
    ]);
    await firstValueFrom(api.cancelStream(hubUrl, created.stream.session_id)).catch(() => undefined);
    if (outcomes.some(value => (
      value.status === 'rejected'
      && observedHttpStatus(value.reason) === 429
    ))) return 1;
  }
  return 0;
}

function validateVoiceObservationCursor(value: SemanticVoiceObservationCursor): void {
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || !value.profileId
    || !value.streamId
    || !value.liveRunId
    || !Number.isSafeInteger(value.partialLatencyMs)
    || value.partialLatencyMs < 0
  ) throw new Error('semantic_voice_observation_cursor_invalid');
}

function hasPendingCorrections(response: { segments?: readonly { correction_status?: string }[] }): boolean {
  return (response.segments ?? []).some(segment => (
    ['queued', 'pending', 'processing'].includes(String(segment.correction_status || ''))
  ));
}

function syntheticPcm(durationMs: number, seed = 1): Uint8Array {
  const samples = Math.max(1, Math.round(16_000 * durationMs / 1_000));
  const bytes = new Uint8Array(samples * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < samples; index += 1) {
    const sample = Math.round(Math.sin((index + seed) / 19) * 1_024);
    view.setInt16(index * 2, sample, true);
  }
  return bytes;
}

function syntheticWav(durationMs: number, seed: number): Blob {
  const pcm = syntheticPcm(durationMs, seed);
  const bytes = new Uint8Array(44 + pcm.byteLength);
  const view = new DataView(bytes.buffer);
  writeAscii(bytes, 0, 'RIFF');
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeAscii(bytes, 8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16_000, true);
  view.setUint32(28, 32_000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(bytes, 36, 'data');
  view.setUint32(40, pcm.byteLength, true);
  bytes.set(pcm, 44);
  pcm.fill(0);
  return new Blob([bytes], { type: 'audio/wav' });
}

function writeAscii(target: Uint8Array, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) target[offset + index] = value.charCodeAt(index);
}

async function prepareHubPeer(fixture: SemanticPeerHubFixture): Promise<void> {
  const ports = requireProductPorts();
  validateHubFixture(fixture);
  const keys = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']) as CryptoKeyPair;
  const publicBytes = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
  const fingerprint = await sha256Bytes(publicBytes);
  const keyId = `speech-sign-${fingerprint.slice(0, 32)}`;
  // A page reload rotates the ephemeral signing key, but the protocol replay
  // window is scoped to the peer/session/epoch/traffic class rather than the
  // key id. Keep the monotone sequence cursor in sessionStorage so a real
  // reconnect cannot replay sequence 1 under a freshly generated key.
  hubSigningState = { keys, keyId, sequence: readSigningSequence(fixture) };
  await firstValueFrom(ports.syncApi.registerKey(fixture.hubUrl, {
    sessionId: fixture.sessionId,
    pairId: fixture.sessionId,
    audienceId: localAudience(fixture),
    epoch: fixture.epoch,
    consentVersion: fixture.consentVersion,
    keyId,
    publicKeyB64: encodeB64(publicBytes),
    expiresAtMs: boundedExpiry(fixture),
  }));
  publicBytes.fill(0);
}

async function proposeHubEvidence(
  fixture: SemanticPeerHubFixture,
  relayCanary: string,
): Promise<SemanticPeerHubOffer> {
  const ports = requireProductPorts();
  const local = localSubject(fixture);
  if (local !== fixture.senderId || !/^ANANTA_RELAY_CANARY_[A-Za-z0-9]{24,96}$/.test(relayCanary)) {
    throw new Error('semantic_peer_hub_proposal_context_invalid');
  }
  const sourceGroupDigest = await sha256Text(`source:${fixture.sessionId}`);
  const originalCandidates = [
    { revision: 1, authority: 'final', text: relayCanary },
    { revision: 2, authority: 'corrected', text: `${relayCanary}-corrected` },
  ];
  const groupBytes = new TextEncoder().encode(JSON.stringify({
    schema: 'ananta.peer-transcript-evidence.v1',
    turn_id: 'turn-live-relay',
    revision: 2,
    state: 'corrected',
    source_digest: sourceGroupDigest,
    candidates: originalCandidates,
  }));
  const groupDigest = await sha256Bytes(groupBytes);
  const groupId = await speechEvidenceGroupId(sourceGroupDigest, 2);
  const inventoryRootDigest = await sha256Canonical({
    group_id: groupId,
    content_digest: groupDigest,
    byte_length: groupBytes.byteLength,
  });
  const offerId = `speech-offer-${crypto.randomUUID()}`;
  const expiresAtMs = boundedExpiry(fixture);
  const speakerScopeDigest = await speechEvidenceSpeakerScopeDigest(
    fixture.sessionId,
    fixture.epoch,
    fixture.senderId,
  );
  const qualityDigest = await speechEvidenceQualityPolicyDigest();
  const candidateProjections = await Promise.all(originalCandidates.map(async (candidate, index) => ({
    ordinal: index + 1,
    candidateDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-original-candidate.v1', source_group_digest: sourceGroupDigest,
      ordinal: index + 1, revision: candidate.revision, authority: candidate.authority,
      candidate_value: candidate.text,
    }),
    authorityDigest: await sha256Canonical({
      domain: 'ananta.speech-evidence-candidate-authority.v1', authority: candidate.authority,
    }),
    revision: candidate.revision,
  })));
  const selectedCandidateDigest = candidateProjections[1].candidateDigest;
  const comparisonDigest = await speechEvidenceComparisonDigest({
    sourceGroupDigest, revision: 2, originalCandidates: candidateProjections,
    resolutionState: 'resolved', selectedCandidateDigest, unresolvedRegionDigests: [],
  });
  const payload = Object.freeze({
    traffic_class: 'control',
    offer_id: offerId,
    stage: 'proposal',
    inventory_root_digest: inventoryRootDigest,
    direction: 'sender_to_receiver',
    purpose: 'speech_dataset_curation',
    data_classes: ['text_corrections'],
    fields: ['transcript'],
    retention_seconds: 3_600,
    trainer_class: 'speech_adaptation',
    group_ids: [groupId],
    group_previews: [{
      preview_version: SPEECH_EVIDENCE_GROUP_PREVIEW_VERSION,
      group_id: groupId,
      source_group_digest: sourceGroupDigest,
      speaker_scope_digest: speakerScopeDigest,
      quality_basis: 'policy',
      quality_digest: qualityDigest,
      resolution_digest: await speechEvidenceResolutionDigest(sourceGroupDigest, 2),
      original_candidates: candidateProjections.map(candidate => ({
        ordinal: candidate.ordinal, candidate_digest: candidate.candidateDigest,
        authority_digest: candidate.authorityDigest, revision: candidate.revision,
      })),
      resolution_state: 'resolved',
      selected_candidate_digest: selectedCandidateDigest,
      unresolved_region_digests: [],
      comparison_digest: comparisonDigest,
      revision: 2,
      size_bytes: groupBytes.byteLength,
    }],
    total_bytes: groupBytes.byteLength,
    sender_consent_digest: fixture.senderConsentDigest,
    recipient_consent_digest: fixture.recipientConsentDigest,
    scope_digest: fixture.scopeDigest,
  });
  const message = await signHubMessage(fixture, 'offer', payload, expiresAtMs);
  const record = await firstValueFrom(ports.syncApi.propose(fixture.hubUrl, message));
  assertOfferRecord(record, fixture, offerId, groupId, 'proposed');
  return Object.freeze({
    offerId,
    inventoryRootDigest,
    groupId,
    groupB64: encodeB64(groupBytes),
    totalBytes: groupBytes.byteLength,
    expiresAtMs,
    value: payload,
    signedMessage: message,
    record,
  });
}

async function acceptHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubAcceptanceResult> {
  const ports = requireProductPorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_acceptance_context_invalid');
  }
  const verified = await verifyPeerEvidenceOfferPreview({
    pairId: fixture.sessionId,
    epoch: fixture.epoch,
    speakerId: fixture.senderId,
    groupIds: offer.record.groupIds,
    totalBytes: offer.record.totalBytes,
    payload: { group_previews: offer.record.groupPreviews.map(value => value.value) },
    expectedPreviewDigest: offer.record.groupPreviewDigest,
    currentSourceRevisions: new Map(
      offer.record.groupPreviews.map(value => [value.sourceGroupDigest, value.revision]),
    ),
  });
  const selectedClasses = [...offer.record.dataClasses];
  const previewView = Object.freeze({
    offerId: offer.record.offerId,
    direction: offer.record.direction,
    purpose: offer.record.purpose,
    dataClasses: offer.record.dataClasses,
    fields: offer.record.fields,
    retentionSeconds: offer.record.retentionSeconds,
    trainerClass: offer.record.trainerClass,
    groupCount: offer.record.groupIds.length,
    groupPreviews: verified.previews,
    previewVerified: true,
    totalBytes: offer.record.totalBytes,
    senderConsentVersion: fixture.consentVersion,
    recipientConsentVersion: fixture.consentVersion,
    state: 'proposed',
    action: 'accept',
    expiresAtMs: offer.record.expiresAtMs,
  });
  const productAcceptPolicyUsed = peerEvidenceAcceptEnabled(previewView, false, selectedClasses);
  const signedPreviewVisible = previewView.previewVerified && previewView.groupPreviews.length > 0;
  const comparisonPreviewVisible = previewView.groupPreviews.every(value => (
    value.originalCandidates.length >= 2
    && value.resolutionState === 'resolved'
    && value.selectedCandidateDigest !== null
    && value.unresolvedRegionDigests.length === 0
  ));
  const productUiProjectionVisible = ports.renderOfferPreview(previewView);
  if (
    !signedPreviewVisible
    || !comparisonPreviewVisible
    || !productAcceptPolicyUsed
    || !productUiProjectionVisible
  ) {
    throw new Error('semantic_peer_hub_product_accept_policy_denied');
  }
  const payload = buildPeerEvidenceAcceptancePayload({
    offer: {
      ...offer.record,
      groupPreviews: verified.previews,
    },
    acceptedClasses: selectedClasses,
    retentionSeconds: offer.record.retentionSeconds,
    trainerClass: offer.record.trainerClass === 'speech_adaptation' ? 'speech_adaptation' : 'none',
    recipientConsentDigest: fixture.recipientConsentDigest,
  });
  const message = await signHubMessage(fixture, 'offer', payload, offer.expiresAtMs);
  const accepted = await firstValueFrom(ports.syncApi.accept(fixture.hubUrl, message));
  assertOfferRecord(accepted, fixture, offer.offerId, offer.groupId, 'accepted');
  const authorized = await firstValueFrom(ports.syncApi.authorizeTransfer(fixture.hubUrl, offer.offerId));
  if (!authorized.transferStarted || authorized.state !== 'accepted') {
    throw new Error('semantic_peer_hub_transfer_not_authorized');
  }
  return Object.freeze({
    signedPreviewVisible,
    comparisonPreviewVisible,
    productAcceptPolicyUsed,
    productUiProjectionVisible,
  });
}

async function transferHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<void> {
  const ports = requireProductPorts();
  if (localSubject(fixture) !== fixture.senderId) {
    throw new Error('semantic_peer_hub_transfer_context_invalid');
  }
  const clear = decodeB64(offer.groupB64);
  const contentKey = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: asArrayBuffer(nonce) }, contentKey, asArrayBuffer(clear),
  ));
  const ciphertextDigest = await sha256Bytes(ciphertext);
  const message = await signHubMessage(fixture, 'chunk', {
    traffic_class: 'evidence_bulk',
    offer_id: offer.offerId,
    group_id: offer.groupId,
    chunk_index: 0,
    chunk_count: 1,
    plaintext_bytes: clear.byteLength,
    plaintext_digest: await sha256Bytes(clear),
    ciphertext_digest: ciphertextDigest,
    nonce_b64: encodeB64(nonce),
    ciphertext_b64: encodeB64(ciphertext),
  }, offer.expiresAtMs);
  const relay = await relayEnvelope(fixture, message, ciphertext, ciphertextDigest);
  const transfer = await firstValueFrom(ports.syncApi.appendChunk(fixture.hubUrl, message, relay));
  if (transfer.groupId !== offer.groupId || transfer.chunkCount !== 1 || transfer.state !== 'active') {
    throw new Error('semantic_peer_hub_chunk_not_registered');
  }
  clear.fill(0);
  nonce.fill(0);
  ciphertext.fill(0);
}

/**
 * Exercise the real Hub transfer route with out-of-order delivery and an
 * exact replay. The result is derived exclusively from returned transfer
 * records and the observed HTTP conflict; no synthetic outcome map is used.
 */
async function exerciseHubEvidenceTransfer(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubTransferExerciseResult> {
  const ports = requireProductPorts();
  if (localSubject(fixture) !== fixture.senderId || fixture.transport !== 'hub_relay') {
    throw new Error('semantic_peer_hub_transfer_context_invalid');
  }
  const clear = decodeB64(offer.groupB64);
  const splitAt = Math.max(1, Math.floor(clear.byteLength / 2));
  const parts = [clear.slice(0, splitAt), clear.slice(splitAt)];
  let backendRouteCount = 0;
  const contentKey = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 }, false, ['encrypt'],
  );
  const messages: Array<{ message: SpeechEvidenceMessage; relay: SemanticDataChannelMessage }> = [];
  try {
    for (const [index, part] of parts.entries()) {
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: asArrayBuffer(nonce) }, contentKey, asArrayBuffer(part),
      ));
      const ciphertextDigest = await sha256Bytes(ciphertext);
      const message = await signHubMessage(fixture, 'chunk', {
        traffic_class: 'evidence_bulk',
        offer_id: offer.offerId,
        group_id: offer.groupId,
        chunk_index: index,
        chunk_count: parts.length,
        plaintext_bytes: part.byteLength,
        plaintext_digest: await sha256Bytes(part),
        ciphertext_digest: ciphertextDigest,
        nonce_b64: encodeB64(nonce),
        ciphertext_b64: encodeB64(ciphertext),
      }, offer.expiresAtMs);
      messages.push({
        message,
        relay: await relayEnvelope(fixture, message, ciphertext, ciphertextDigest),
      });
      nonce.fill(0);
      ciphertext.fill(0);
    }

    const outOfOrder = await firstValueFrom(ports.syncApi.appendChunk(
      fixture.hubUrl, messages[1].message, messages[1].relay,
    ));
    backendRouteCount += 1;
    let duplicateRejected = false;
    try {
      await firstValueFrom(ports.syncApi.appendChunk(
        fixture.hubUrl, messages[1].message, messages[1].relay,
      ));
      backendRouteCount += 1;
    } catch (error) {
      backendRouteCount += 1;
      duplicateRejected = observedHttpStatus(error) === 409;
    }
    const completedUpload = await firstValueFrom(ports.syncApi.appendChunk(
      fixture.hubUrl, messages[0].message, messages[0].relay,
    ));
    backendRouteCount += 1;
    const status = await firstValueFrom(ports.syncApi.transferStatus(
      fixture.hubUrl, offer.offerId, offer.groupId,
    ));
    backendRouteCount += 1;
    const reorderRecovered = outOfOrder.firstMissingIndex === 0
      && completedUpload.chunkCount === 2
      && status.chunkCount === 2
      && status.inFlightBytes === offer.totalBytes;
    if (!duplicateRejected || !reorderRecovered) {
      throw new Error('semantic_peer_hub_transfer_exercise_failed');
    }
    return Object.freeze({
      backendRouteCount,
      forcedRelayUsed: fixture.transport === 'hub_relay',
      duplicateRejected,
      reorderRecovered,
    });
  } finally {
    clear.fill(0);
    for (const part of parts) part.fill(0);
  }
}

async function recoverHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<boolean> {
  const ports = requireProductPorts();
  const offers = await firstValueFrom(ports.syncApi.listOffers(fixture.hubUrl, {
    sessionId: fixture.sessionId,
    pairId: fixture.sessionId,
    epoch: fixture.epoch,
  }));
  const recovered = offers.find(value => value.offerId === offer.offerId);
  if (!recovered || recovered.groupPreviewDigest !== offer.record.groupPreviewDigest) return false;
  const transfer = await firstValueFrom(ports.syncApi.transferStatus(
    fixture.hubUrl, offer.offerId, offer.groupId,
  ));
  return transfer.chunkCount === 2 && transfer.state === 'active';
}

async function revokeHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<boolean> {
  const ports = requireProductPorts();
  const reasonCode = 'speech_evidence_e2e_user_revoked';
  const invalidated = await firstValueFrom(ports.syncApi.invalidate(
    fixture.hubUrl, offer.offerId, reasonCode,
  ));
  return invalidated.state === 'invalidated'
    && invalidated.invalidationReason === reasonCode;
}

async function acknowledgeHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<void> {
  const ports = requireProductPorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_ack_context_invalid');
  }
  const status = await firstValueFrom(ports.syncApi.transferStatus(
    fixture.hubUrl, offer.offerId, offer.groupId,
  ));
  const indices = Array.from({ length: status.chunkCount }, (_, index) => index);
  const message = await signHubMessage(fixture, 'chunk_ack', {
    traffic_class: 'control',
    offer_id: offer.offerId,
    group_id: offer.groupId,
    acknowledged_indices: indices,
    first_missing_index: status.chunkCount,
    received_bytes: offer.totalBytes,
    complete: true,
  }, offer.expiresAtMs);
  const transfer = await firstValueFrom(ports.syncApi.acknowledgeChunk(fixture.hubUrl, message));
  if (transfer.state !== 'completed' || transfer.firstMissingIndex !== status.chunkCount) {
    throw new Error('semantic_peer_hub_ack_not_completed');
  }
}

async function curateHubEvidence(
  fixture: SemanticPeerHubFixture,
  offer: SemanticPeerHubOffer,
): Promise<SemanticPeerHubCurationResult> {
  const ports = requireProductPorts();
  if (localSubject(fixture) !== fixture.recipientId) {
    throw new Error('semantic_peer_hub_curation_context_invalid');
  }
  const quarantined = [offer.groupId];
  const resolutionDigest = await sha256Canonical({
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    quarantined_group_ids: quarantined,
  });
  const resultDigest = await sha256Canonical({ accepted: [], quarantined, rejected: [] });
  const message = await signHubMessage(fixture, 'receipt', {
    traffic_class: 'control',
    receipt_id: `curation-request-${crypto.randomUUID()}`,
    offer_id: offer.offerId,
    inventory_root_digest: offer.inventoryRootDigest,
    resolution_digest: resolutionDigest,
    accepted_group_ids: [],
    rejected_group_ids: [],
    quarantined_group_ids: quarantined,
    consent_digest: fixture.recipientConsentDigest,
    policy_digest: PEER_CURATION_REQUEST_POLICY_DIGEST,
    result_digest: resultDigest,
  }, offer.expiresAtMs);
  // exerciseHubEvidenceTransfer deliberately sends two signed plaintext
  // chunks out of order. Curation must disclose those exact ACK-bound chunk
  // boundaries; sending the rejoined group as one chunk correctly fails the
  // Hub's digest/sequence binding with 409.
  const clear = decodeB64(offer.groupB64);
  const splitAt = Math.max(1, Math.floor(clear.byteLength / 2));
  const chunks = [clear.slice(0, splitAt), clear.slice(splitAt)];
  try {
    const response = await ports.curation.request({
      hubUrl: fixture.hubUrl,
      binding: {
        offerId: offer.offerId,
        inventoryRootDigest: offer.inventoryRootDigest,
        pairId: fixture.sessionId,
        direction: 'sender_to_receiver',
        consentDigest: fixture.recipientConsentDigest,
        groupIds: [offer.groupId],
      },
      message,
      groups: [{ groupId: offer.groupId, chunksB64: chunks.map(encodeB64) }],
    });
    return curationSummary(response);
  } finally {
    clear.fill(0);
    for (const chunk of chunks) chunk.fill(0);
  }
}

function curationSummary(response: SpeechEvidenceHubCurationResponse): SemanticPeerHubCurationResult {
  return Object.freeze({
    receiptVerified: true,
    state: response.curation.state,
    acceptedGroupCount: response.curation.receipt.acceptedGroupIds.length,
    curationTaskQueued: Boolean(response.curation.curationTaskId),
    datasetReserved: Boolean(response.curation.datasetId),
  });
}

async function signHubMessage(
  fixture: SemanticPeerHubFixture,
  type: SpeechEvidenceMessageType,
  payload: Readonly<Record<string, unknown>>,
  expiresAtMs: number,
): Promise<SpeechEvidenceMessage> {
  const state = requireHubSigningState();
  const issuedAtMs = Date.now();
  const unsigned: SpeechEvidenceMessage = {
    protocol_version: type === 'offer'
      ? SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION
      : SPEECH_EVIDENCE_PROTOCOL_VERSION,
    message_type: type,
    message_id: `speech-e2e-${type}-${crypto.randomUUID()}`,
    session_id: fixture.sessionId,
    pair_id: fixture.sessionId,
    sender_id: localSubject(fixture),
    audience_id: localAudience(fixture),
    epoch: fixture.epoch,
    sequence: nextSigningSequence(fixture, state),
    consent_version: fixture.consentVersion,
    key_id: state.keyId,
    issued_at_ms: issuedAtMs,
    expires_at_ms: Math.min(expiresAtMs, issuedAtMs + 5 * 60_000),
    payload_digest: await sha256Canonical(payload),
    payload,
    signature_algorithm: 'Ed25519',
    signature_b64: encodeB64(new Uint8Array(64)),
  };
  const signature = new Uint8Array(await crypto.subtle.sign(
    'Ed25519', state.keys.privateKey, new TextEncoder().encode(canonicalSigningJson(unsigned)),
  ));
  const signed = validateSpeechEvidenceMessage({ ...unsigned, signature_b64: encodeB64(signature) });
  signature.fill(0);
  return signed;
}

async function relayEnvelope(
  fixture: SemanticPeerHubFixture,
  message: SpeechEvidenceMessage,
  ciphertext: Uint8Array,
  ciphertextDigest: string,
): Promise<SemanticDataChannelMessage> {
  return Object.freeze({
    version: 'ananta.webrtc-datachannel.v1',
    traffic_class: 'evidence_bulk',
    message_id: `relay-${message.message_id}`,
    session_id: fixture.sessionId,
    epoch: fixture.epoch,
    sender_id: fixture.senderId,
    audience_id: fixture.recipientId,
    sequence: message.sequence,
    expires_at_ms: message.expires_at_ms,
    compression: 'none',
    security: Object.freeze({ algorithm: 'AES-GCM-256', key_id: 'semantic-e2e-confirmed-pair-key' }),
    payload_bytes: ciphertext.byteLength,
    payload_digest: ciphertextDigest,
    ciphertext: encodeB64(ciphertext),
  });
}

function assertOfferRecord(
  record: SpeechEvidenceOfferRecord,
  fixture: SemanticPeerHubFixture,
  offerId: string,
  groupId: string,
  state: string,
): void {
  if (
    record.offerId !== offerId
    || record.sessionId !== fixture.sessionId
    || record.pairId !== fixture.sessionId
    || record.epoch !== fixture.epoch
    || record.senderId !== fixture.senderId
    || record.recipientId !== fixture.recipientId
    || record.state !== state
    || record.groupIds.length !== 1
    || record.groupIds[0] !== groupId
  ) throw new Error('semantic_peer_hub_offer_binding_invalid');
}

function validateHubFixture(value: SemanticPeerHubFixture): void {
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || !value.sessionId
    || !Number.isSafeInteger(value.epoch)
    || value.epoch < 1
    || value.senderId === value.recipientId
    || !/^[0-9a-f]{64}$/.test(value.senderConsentDigest)
    || !/^[0-9a-f]{64}$/.test(value.recipientConsentDigest)
    || !/^[0-9a-f]{64}$/.test(value.scopeDigest)
    || value.consentVersion !== 1
    || !['hub_relay', 'webrtc'].includes(value.transport)
    || value.expiresAtMs <= Date.now()
  ) throw new Error('semantic_peer_hub_fixture_invalid');
}

function observedHttpStatus(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const value = error as { status?: unknown; error?: { status?: unknown } };
  const status = Number(value.status ?? value.error?.status ?? 0);
  return Number.isSafeInteger(status) ? status : 0;
}

function localSubject(fixture: SemanticPeerHubFixture): string {
  const token = localStorage.getItem('ananta.user.token') || '';
  try {
    const payload = JSON.parse(new TextDecoder().decode(decodeB64Url(token.split('.')[1] || ''))) as { sub?: unknown };
    const subject = String(payload.sub || '');
    if (subject === fixture.senderId || subject === fixture.recipientId) return subject;
  } catch { /* fail below */ }
  throw new Error('semantic_peer_hub_identity_missing');
}

function localAudience(fixture: SemanticPeerHubFixture): string {
  return localSubject(fixture) === fixture.senderId ? fixture.recipientId : fixture.senderId;
}

function signingSequenceStorageKey(fixture: SemanticPeerHubFixture): string {
  return [
    'ananta.semantic-media-e2e.signing-sequence',
    fixture.sessionId,
    String(fixture.epoch),
    localSubject(fixture),
  ].join(':');
}

function readSigningSequence(fixture: SemanticPeerHubFixture): number {
  const value = Number(sessionStorage.getItem(signingSequenceStorageKey(fixture)) || '0');
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function nextSigningSequence(
  fixture: SemanticPeerHubFixture,
  state: { sequence: number },
): number {
  state.sequence += 1;
  sessionStorage.setItem(signingSequenceStorageKey(fixture), String(state.sequence));
  return state.sequence;
}

function boundedExpiry(fixture: SemanticPeerHubFixture): number {
  return Math.min(fixture.expiresAtMs, Date.now() + 5 * 60_000);
}

function requireProductPorts(): SemanticMediaPairProductPorts {
  if (!productPorts) throw new Error('semantic_peer_hub_product_facade_unavailable');
  return productPorts;
}

function requireHubSigningState(): { keys: CryptoKeyPair; keyId: string; sequence: number } {
  if (!hubSigningState) throw new Error('semantic_peer_hub_signing_state_missing');
  return hubSigningState;
}

async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', asArrayBuffer(value)));
  return [...digest].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

function decodeB64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
  return decodeB64(padded);
}


async function waitUntil(predicate: () => boolean, timeoutMs: number, reason: string): Promise<void> {
  const started = performance.now();
  while (!predicate()) {
    if (performance.now() - started > timeoutMs) throw new Error(reason);
    await delay(10);
  }
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function encodeB64(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function decodeB64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function asArrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}
