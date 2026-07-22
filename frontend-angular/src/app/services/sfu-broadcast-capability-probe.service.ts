import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';

import type {
  BrowserMediaCapabilityObservationContract,
  SfuBroadcastJsonObject,
} from './sfu-broadcast-contracts';

export type SfuCapabilityCodecBucket =
  | 'unknown' | 'unsupported' | 'audio_opus' | 'video_vp8' | 'video_h264' | 'video_vp9' | 'video_av1';
export type SfuCapabilityLayeringBucket = 'unknown' | 'unsupported' | 'simulcast' | 'svc';
export type SfuCapabilityTransformBucket = 'unknown' | 'unsupported' | 'available';
export type SfuCapabilityDecodeBucket =
  | 'unknown' | 'unsupported' | 'audio_realtime' | 'video_baseline' | 'video_enhanced';
export type SfuCapabilityEvidenceBucket =
  | 'not_observed' | 'static_api_presence' | 'static_capability_query';

export interface SfuBrowserCapabilityBucket extends SfuBroadcastJsonObject {
  readonly codec_bucket: SfuCapabilityCodecBucket;
  readonly layering_bucket: SfuCapabilityLayeringBucket;
  readonly encoded_transform_bucket: SfuCapabilityTransformBucket;
  readonly decode_bucket: SfuCapabilityDecodeBucket;
  readonly evidence_bucket: SfuCapabilityEvidenceBucket;
}

export interface SfuBrowserCapabilityScope {
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly admissionEpoch: number;
  readonly membershipEpoch: number;
}

export interface SfuStaticCodecCapability {
  readonly mimeType: string;
  readonly scalabilityModes?: readonly string[];
}

export interface SfuBrowserCapabilityProbeEnvironment {
  nowMs(): number;
  randomBytes(length: number): Uint8Array;
  senderCodecs(kind: 'audio' | 'video'): readonly SfuStaticCodecCapability[] | null;
  receiverCodecs(kind: 'audio' | 'video'): readonly SfuStaticCodecCapability[] | null;
  encodedTransformAvailable(): boolean;
  simulcastApiAvailable(): boolean;
}

export interface SfuBrowserCapabilityProbeResult {
  readonly observation: BrowserMediaCapabilityObservationContract;
  readonly status: 'fully_supported' | 'partially_supported' | 'unknown' | 'unsupported';
  readonly parentFallbackRequired: boolean;
  readonly reasonCode: string;
}

interface ScopeState {
  readonly scopeKey: string;
  readonly pseudonym: string;
  readonly createdAtMs: number;
  sequence: number;
}

const TTL_SECONDS = 300 as const;
const PSEUDONYM_ROTATION_SECONDS = 900 as const;
const COMBINATIONS_MAX = 8 as const;
const REPORT_BYTES_MAX = 2048 as const;
const CODEC_ORDER: readonly SfuCapabilityCodecBucket[] = Object.freeze([
  'audio_opus', 'video_vp8', 'video_h264', 'video_vp9', 'video_av1',
]);

export const SFU_BROWSER_CAPABILITY_PROBE_ENVIRONMENT =
  new InjectionToken<SfuBrowserCapabilityProbeEnvironment>('SFU_BROWSER_CAPABILITY_PROBE_ENVIRONMENT', {
    providedIn: 'root',
    factory: nativeProbeEnvironment,
  });

@Injectable({ providedIn: 'root' })
export class SfuBroadcastCapabilityProbeService implements OnDestroy {
  private readonly environment = inject(SFU_BROWSER_CAPABILITY_PROBE_ENVIRONMENT);
  private readonly scopes = new Map<string, ScopeState>();

  probe(scope: SfuBrowserCapabilityScope): SfuBrowserCapabilityProbeResult {
    const normalized = normalizeScope(scope);
    const nowMs = this.environment.nowMs();
    const state = this.scopeState(normalized, nowMs);
    state.sequence += 1;
    let buckets: readonly SfuBrowserCapabilityBucket[];
    let reasonCode = 'browser_capability_static_query_complete';
    try {
      buckets = this.staticBuckets();
    } catch {
      buckets = Object.freeze([unknownBucket()]);
      reasonCode = 'browser_capability_probe_unavailable';
    }
    const observation = this.observation(normalized, state, nowMs, buckets);
    const status = classify(buckets);
    if (status === 'unknown') reasonCode = 'browser_capability_unknown_parent_fallback';
    if (status === 'unsupported') reasonCode = 'browser_capability_unsupported_parent_fallback';
    if (status === 'partially_supported') reasonCode = 'browser_capability_partial_parent_fallback';
    return Object.freeze({
      observation,
      status,
      parentFallbackRequired: status !== 'fully_supported',
      reasonCode,
    });
  }

  clearScope(scope: SfuBrowserCapabilityScope): void {
    this.scopes.delete(scopeKey(normalizeScope(scope)));
  }

  clearAll(): void {
    this.scopes.clear();
  }

  ngOnDestroy(): void {
    this.clearAll();
  }

  private staticBuckets(): readonly SfuBrowserCapabilityBucket[] {
    const audioSend = codecMap(this.environment.senderCodecs('audio'));
    const audioReceive = codecMap(this.environment.receiverCodecs('audio'));
    const videoSend = codecMap(this.environment.senderCodecs('video'));
    const videoReceive = codecMap(this.environment.receiverCodecs('video'));
    if (!audioSend || !audioReceive || !videoSend || !videoReceive) {
      return Object.freeze([unknownBucket()]);
    }
    const encodedTransform: SfuCapabilityTransformBucket =
      this.environment.encodedTransformAvailable() ? 'available' : 'unsupported';
    const rows: SfuBrowserCapabilityBucket[] = [];
    for (const codec of CODEC_ORDER) {
      const send = codec === 'audio_opus' ? audioSend.get(codec) : videoSend.get(codec);
      const receive = codec === 'audio_opus' ? audioReceive.get(codec) : videoReceive.get(codec);
      if (!send || !receive) continue;
      rows.push(Object.freeze({
        codec_bucket: codec,
        layering_bucket: layering(codec, send, this.environment.simulcastApiAvailable()),
        encoded_transform_bucket: encodedTransform,
        decode_bucket: decode(codec),
        evidence_bucket: 'static_capability_query',
      }));
    }
    if (rows.length === 0) {
      return Object.freeze([Object.freeze({
        codec_bucket: 'unsupported',
        layering_bucket: 'unsupported',
        encoded_transform_bucket: encodedTransform,
        decode_bucket: 'unsupported',
        evidence_bucket: 'static_capability_query',
      })]);
    }
    return Object.freeze(rows.slice(0, COMBINATIONS_MAX));
  }

  private observation(
    scope: SfuBrowserCapabilityScope,
    state: ScopeState,
    nowMs: number,
    buckets: readonly SfuBrowserCapabilityBucket[],
  ): BrowserMediaCapabilityObservationContract {
    const base = {
      schema: 'ananta.browser-media-capability-observation.v1' as const,
      schema_version: 1 as const,
      capability_version: 'coarse-v1' as const,
      tenant_ref: scope.tenantRef,
      room_ref: scope.roomRef,
      admission_epoch: scope.admissionEpoch,
      membership_epoch: scope.membershipEpoch,
      browser_instance_pseudonym: state.pseudonym,
      sequence: state.sequence,
      issued_at: utcSecond(nowMs),
      ttl_seconds: TTL_SECONDS,
      pseudonym_rotation_seconds: PSEUDONYM_ROTATION_SECONDS,
      capability_bucket_combinations_max: COMBINATIONS_MAX,
      report_bytes_max: REPORT_BYTES_MAX,
      authorization_effect: 'none' as const,
    };
    let bounded = [...buckets];
    while (bounded.length > 1 && encodedBytes({ ...base, capability_buckets: bounded }) > REPORT_BYTES_MAX) {
      bounded = bounded.slice(0, -1);
    }
    const result: BrowserMediaCapabilityObservationContract = Object.freeze({
      ...base,
      capability_buckets: Object.freeze(bounded),
    });
    if (encodedBytes(result) > REPORT_BYTES_MAX) throw new Error('browser_capability_report_budget_exceeded');
    return result;
  }

  private scopeState(scope: SfuBrowserCapabilityScope, nowMs: number): ScopeState {
    const key = scopeKey(scope);
    const existing = this.scopes.get(key);
    if (existing && nowMs - existing.createdAtMs < PSEUDONYM_ROTATION_SECONDS * 1000) return existing;
    const next: ScopeState = {
      scopeKey: key,
      pseudonym: `room-bip_${base64Url(this.environment.randomBytes(16))}`,
      createdAtMs: nowMs,
      sequence: 0,
    };
    this.scopes.set(key, next);
    return next;
  }
}

function nativeProbeEnvironment(): SfuBrowserCapabilityProbeEnvironment {
  type CodecRow = { mimeType?: unknown; scalabilityModes?: unknown };
  type CapabilityConstructor = { getCapabilities?: (kind: 'audio' | 'video') => { codecs?: CodecRow[] } | null };
  const runtime = globalThis as unknown as {
    RTCRtpSender?: CapabilityConstructor & { prototype?: Record<string, unknown> };
    RTCRtpReceiver?: CapabilityConstructor;
    RTCRtpScriptTransform?: unknown;
    crypto?: Crypto;
  };
  const read = (source: CapabilityConstructor | undefined, kind: 'audio' | 'video') => {
    if (typeof source?.getCapabilities !== 'function') return null;
    const rows = source.getCapabilities(kind)?.codecs ?? [];
    return rows.flatMap(row => typeof row.mimeType === 'string' ? [Object.freeze({
      mimeType: row.mimeType,
      ...(Array.isArray(row.scalabilityModes)
        ? { scalabilityModes: Object.freeze(row.scalabilityModes.filter(value => typeof value === 'string')) as string[] }
        : {}),
    })] : []);
  };
  return Object.freeze({
    nowMs: () => Date.now(),
    randomBytes: (length: number) => {
      if (!runtime.crypto?.getRandomValues) throw new Error('browser_crypto_unavailable');
      return runtime.crypto.getRandomValues(new Uint8Array(length));
    },
    senderCodecs: (kind: 'audio' | 'video') => read(runtime.RTCRtpSender, kind),
    receiverCodecs: (kind: 'audio' | 'video') => read(runtime.RTCRtpReceiver, kind),
    encodedTransformAvailable: () => Boolean(
      runtime.RTCRtpScriptTransform
      || typeof runtime.RTCRtpSender?.prototype?.['createEncodedStreams'] === 'function',
    ),
    simulcastApiAvailable: () => typeof runtime.RTCRtpSender?.prototype?.['setParameters'] === 'function',
  });
}

function codecMap(rows: readonly SfuStaticCodecCapability[] | null): Map<SfuCapabilityCodecBucket, SfuStaticCodecCapability> | null {
  if (rows === null) return null;
  const result = new Map<SfuCapabilityCodecBucket, SfuStaticCodecCapability>();
  for (const row of rows) {
    const bucket = codecBucket(row.mimeType);
    if (bucket !== 'unknown' && bucket !== 'unsupported' && !result.has(bucket)) result.set(bucket, row);
  }
  return result;
}

function codecBucket(mimeType: string): SfuCapabilityCodecBucket {
  const normalized = mimeType.trim().toLowerCase();
  if (normalized === 'audio/opus') return 'audio_opus';
  if (normalized === 'video/vp8') return 'video_vp8';
  if (normalized === 'video/h264') return 'video_h264';
  if (normalized === 'video/vp9') return 'video_vp9';
  if (normalized === 'video/av1') return 'video_av1';
  return 'unknown';
}

function layering(
  codec: SfuCapabilityCodecBucket,
  capability: SfuStaticCodecCapability,
  simulcastAvailable: boolean,
): SfuCapabilityLayeringBucket {
  if (codec === 'audio_opus') return 'unsupported';
  if ((capability.scalabilityModes ?? []).some(value => /^L[1-3]T[1-3]/i.test(value))) return 'svc';
  return simulcastAvailable ? 'simulcast' : 'unknown';
}

function decode(codec: SfuCapabilityCodecBucket): SfuCapabilityDecodeBucket {
  if (codec === 'audio_opus') return 'audio_realtime';
  if (codec === 'video_vp9' || codec === 'video_av1') return 'video_enhanced';
  return 'video_baseline';
}

function unknownBucket(): SfuBrowserCapabilityBucket {
  return Object.freeze({
    codec_bucket: 'unknown', layering_bucket: 'unknown', encoded_transform_bucket: 'unknown',
    decode_bucket: 'unknown', evidence_bucket: 'not_observed',
  });
}

function classify(buckets: readonly SfuBrowserCapabilityBucket[]): SfuBrowserCapabilityProbeResult['status'] {
  if (buckets.every(value => value.evidence_bucket === 'not_observed')) return 'unknown';
  if (buckets.every(value => value.codec_bucket === 'unsupported')) return 'unsupported';
  const advanced = buckets.some(value => value.codec_bucket.startsWith('video_')
    && (value.layering_bucket === 'simulcast' || value.layering_bucket === 'svc')
    && value.encoded_transform_bucket === 'available');
  return advanced ? 'fully_supported' : 'partially_supported';
}

function normalizeScope(scope: SfuBrowserCapabilityScope): SfuBrowserCapabilityScope {
  const identifier = (value: string, reason: string) => {
    if (typeof value !== 'string' || !value || new TextEncoder().encode(value).byteLength > 128
        || /[\u0000-\u0020\u007f]/.test(value)) throw new Error(reason);
    return value;
  };
  const epoch = (value: number, reason: string) => {
    if (!Number.isSafeInteger(value) || value < 1) throw new Error(reason);
    return value;
  };
  return Object.freeze({
    tenantRef: identifier(scope.tenantRef, 'browser_capability_tenant_invalid'),
    roomRef: identifier(scope.roomRef, 'browser_capability_room_invalid'),
    admissionEpoch: epoch(scope.admissionEpoch, 'browser_capability_admission_epoch_invalid'),
    membershipEpoch: epoch(scope.membershipEpoch, 'browser_capability_membership_epoch_invalid'),
  });
}

function scopeKey(scope: SfuBrowserCapabilityScope): string {
  return `${scope.tenantRef}\u0000${scope.roomRef}\u0000${scope.admissionEpoch}\u0000${scope.membershipEpoch}`;
}

function utcSecond(nowMs: number): string {
  if (!Number.isFinite(nowMs) || nowMs < 0) throw new Error('browser_capability_clock_invalid');
  return new Date(Math.floor(nowMs / 1000) * 1000).toISOString().replace('.000Z', 'Z');
}

function encodedBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function base64Url(bytes: Uint8Array): string {
  if (bytes.length !== 16) throw new Error('browser_capability_randomness_invalid');
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  let bits = 0;
  let value = 0;
  let output = '';
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 6) {
      bits -= 6;
      output += alphabet[(value >>> bits) & 63];
    }
  }
  if (bits > 0) output += alphabet[(value << (6 - bits)) & 63];
  if (output.length !== 22) throw new Error('browser_capability_pseudonym_invalid');
  return output;
}
