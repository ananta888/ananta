import { SpeechEvidenceHubCurationResponse, SpeechEvidenceOfferRecord } from '../services/speech-evidence-sync-api.service';
import { SpeechEvidenceMessage } from '../services/speech-evidence-sync.validators';
import { SemanticDataChannelMessage } from '../services/webrtc-datachannel.service';
import { SemanticPeerHubCurationResult, SemanticPeerHubFixture } from './semantic-media-pair-live-driver.models';

/**
 * Fixture validation, identity lookup, envelope builders and byte/timing
 * helpers shared by the Pair live driver's hub-evidence, direct-path and
 * voice-observation flows.
 */

export function validateHubFixture(value: SemanticPeerHubFixture): void {
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

export function assertOfferRecord(
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

export async function relayEnvelope(
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

export function curationSummary(response: SpeechEvidenceHubCurationResponse): SemanticPeerHubCurationResult {
  return Object.freeze({
    receiptVerified: true,
    state: response.curation.state,
    acceptedGroupCount: response.curation.receipt.acceptedGroupIds.length,
    curationTaskQueued: Boolean(response.curation.curationTaskId),
    datasetReserved: Boolean(response.curation.datasetId),
  });
}

export function observedHttpStatus(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const value = error as { status?: unknown; error?: { status?: unknown } };
  const status = Number(value.status ?? value.error?.status ?? 0);
  return Number.isSafeInteger(status) ? status : 0;
}

export function localSubject(fixture: SemanticPeerHubFixture): string {
  const token = localStorage.getItem('ananta.user.token') || '';
  try {
    const payload = JSON.parse(new TextDecoder().decode(decodeB64Url(token.split('.')[1] || ''))) as { sub?: unknown };
    const subject = String(payload.sub || '');
    if (subject === fixture.senderId || subject === fixture.recipientId) return subject;
  } catch { /* fail below */ }
  throw new Error('semantic_peer_hub_identity_missing');
}

export function localAudience(fixture: SemanticPeerHubFixture): string {
  return localSubject(fixture) === fixture.senderId ? fixture.recipientId : fixture.senderId;
}

export function boundedExpiry(fixture: SemanticPeerHubFixture): number {
  return Math.min(fixture.expiresAtMs, Date.now() + 5 * 60_000);
}

export function syntheticPcm(durationMs: number, seed = 1): Uint8Array {
  const samples = Math.max(1, Math.round(16_000 * durationMs / 1_000));
  const bytes = new Uint8Array(samples * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < samples; index += 1) {
    const sample = Math.round(Math.sin((index + seed) / 19) * 1_024);
    view.setInt16(index * 2, sample, true);
  }
  return bytes;
}

export function syntheticWav(durationMs: number, seed: number): Blob {
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

export async function sha256Bytes(value: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', asArrayBuffer(value)));
  return [...digest].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

export function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

export async function waitUntil(predicate: () => boolean, timeoutMs: number, reason: string): Promise<void> {
  const started = performance.now();
  while (!predicate()) {
    if (performance.now() - started > timeoutMs) throw new Error(reason);
    await delay(10);
  }
}

export function delay(ms: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

export function encodeB64(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function decodeB64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function decodeB64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4);
  return decodeB64(padded);
}

export function asArrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}
