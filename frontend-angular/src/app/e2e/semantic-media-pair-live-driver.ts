import { SemanticDataChannelMessage } from '../services/webrtc-datachannel.service';
import {
  SemanticMediaPairHubProductDriver,
  SemanticMediaPairProductPorts,
  SemanticPeerHubFixture,
} from './semantic-media-pair-live-driver.models';
import {
  asArrayBuffer,
  boundedExpiry,
  encodeB64,
  localAudience,
  localSubject,
  sha256Bytes,
  validateHubFixture,
  waitUntil,
} from './semantic-media-pair-live-driver.support';
import {
  acceptHubEvidence,
  acknowledgeHubEvidence,
  curateHubEvidence,
  exerciseHubEvidenceTransfer,
  prepareHubPeer,
  proposeHubEvidence,
  recoverHubEvidence,
  revokeHubEvidence,
  transferHubEvidence,
} from './semantic-media-pair-live-hub-evidence';
import {
  resumeVoiceProductObservation,
  startVoiceProductObservation,
} from './semantic-media-pair-live-voice-observation';

export type {
  SemanticMediaPairHubProductDriver,
  SemanticMediaPairProductPorts,
  SemanticPeerHubAcceptanceResult,
  SemanticPeerHubCurationResult,
  SemanticPeerHubFixture,
  SemanticPeerHubOffer,
  SemanticPeerHubTransferExerciseResult,
  SemanticVoiceObservationCursor,
  SemanticVoiceObservationResult,
} from './semantic-media-pair-live-driver.models';

/**
 * In-page product driver of the semantic-media Pair live E2E gate. It owns
 * the installed product ports and the direct WebRTC product path and exposes
 * the hub-evidence and voice-observation flows (own modules) on window.
 */

const E2E_QUERY = 'semanticMediaLiveE2e';
let productPorts: SemanticMediaPairProductPorts | null = null;
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
        prepare: (fixture) => prepareHubPeer(requireProductPorts, fixture),
        propose: (fixture, canary) => proposeHubEvidence(requireProductPorts, fixture, canary),
        accept: (fixture, offer) => acceptHubEvidence(requireProductPorts, fixture, offer),
        transfer: (fixture, offer) => transferHubEvidence(requireProductPorts, fixture, offer),
        exerciseTransfer: (fixture, offer) => exerciseHubEvidenceTransfer(requireProductPorts, fixture, offer),
        recover: (fixture, offer) => recoverHubEvidence(requireProductPorts, fixture, offer),
        revoke: (fixture, offer) => revokeHubEvidence(requireProductPorts, fixture, offer),
        acknowledge: (fixture, offer) => acknowledgeHubEvidence(requireProductPorts, fixture, offer),
        curate: (fixture, offer) => curateHubEvidence(requireProductPorts, fixture, offer),
        openDirect: (fixture, initiator) => openDirectProductPath(fixture, initiator),
        sendDirect: (fixture, canary) => sendDirectProductPath(fixture, canary),
        receiveDirect: (messageId) => receiveDirectProductPath(messageId),
        closeDirect: () => closeDirectProductPath(),
        startVoiceObservation: (hubUrl) => startVoiceProductObservation(requireProductPorts, hubUrl),
        resumeVoiceObservation: (cursor) => resumeVoiceProductObservation(requireProductPorts, cursor),
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

function requireProductPorts(): SemanticMediaPairProductPorts {
  if (!productPorts) throw new Error('semantic_peer_hub_product_facade_unavailable');
  return productPorts;
}
