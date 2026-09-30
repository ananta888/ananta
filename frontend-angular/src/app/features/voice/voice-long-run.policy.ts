import { VoiceLongRunLivePreviewUpdate } from './voice-long-run-live-preview';
import { VoiceLongRunRecoveryMetadata } from './voice-long-run-recovery';
import { VoiceLongRunSpoolMetadata } from './voice-long-run-spool';
import { VoiceLongRunTimelineSnapshot } from './voice-long-run-timeline';
import {
  VoiceLongRunCreateRequest,
  VoiceLongRunLease,
  VoiceLongRunResponse,
  VoiceLongRunSegmentUploadResponse,
  VoiceLongRunState,
} from './voice.models';

/**
 * Observer contract, timing constants and pure run/cursor/error policy of
 * long-run capture. Nothing here holds state or performs I/O.
 */

export interface VoiceLongRunObserver {
  runUpdated?(response: VoiceLongRunResponse): void;
  timelineUpdated?(snapshot: VoiceLongRunTimelineSnapshot): void;
  progress?(capturedMilliseconds: number): void;
  buffered?(metadata: VoiceLongRunSpoolMetadata, queuedSegments: number): void;
  segmentUploaded?(response: VoiceLongRunSegmentUploadResponse, queuedSegments: number): void;
  segmentFailed?(sequence: number, error: unknown): void;
  gap?(sequence: number): void;
  gapsUpdated?(sequences: readonly number[]): void;
  recoveryUpdated?(metadata: VoiceLongRunRecoveryMetadata): void;
  livePreviewStarted?(segmentSequence: number): void;
  livePreview?(update: VoiceLongRunLivePreviewUpdate): void;
  livePreviewUnavailable?(error: unknown): void;
  connection?(state: 'online' | 'retrying'): void;
  stopping?(reason: string): void;
  stopped?(response: VoiceLongRunResponse, reason: string): void;
  error?(error: unknown): void;
}

export interface VoiceLongRunCursor {
  readonly nextSequence: number;
  readonly timelineMilliseconds: number;
  readonly durableNextSequence: number;
  readonly durableTimelineMilliseconds: number;
  readonly completedTimelineMilliseconds: number;
  readonly gaps: number[];
}

export const HEARTBEAT_MILLISECONDS = 15_000;
export const MAX_STOP_UPLOAD_ATTEMPTS = 6;
export const RETRY_DELAYS_MILLISECONDS = [1_000, 2_000, 5_000, 10_000, 30_000] as const;
export const MAX_PENDING_PLAINTEXT_SEGMENTS = 2;
export const MAX_PENDING_PLAINTEXT_BYTES = 8 * 1024 * 1024;
export const SPOOL_WRITE_TIMEOUT_MILLISECONDS = 10_000;
export const REVISION_POLL_MILLISECONDS = 1_500;
export const REVISION_POLL_LIMIT = 100;
export const REVISION_RETRY_DELAYS_MILLISECONDS = [1_000, 2_000, 5_000, 10_000, 15_000] as const;
export const MAX_STOP_CORRECTION_ATTEMPTS = 240;
export const STOP_CORRECTION_POLL_MILLISECONDS = 2_500;

export function ensureOperation(generation: number, currentGeneration: number): void {
  if (generation !== currentGeneration) throw new Error('voice.capture.cancelled');
}

/** Resume cursor reconciled from the Hub snapshot, the encrypted spool and the local descriptor. */
export function reconciledCursor(
  response: VoiceLongRunResponse,
  buffered: VoiceLongRunSpoolMetadata[],
  descriptor?: VoiceLongRunRecoveryMetadata,
): VoiceLongRunCursor {
  const actualSegments = (response.segments || []).filter((item) => item.status !== 'gap');
  const durableNextSequence = Math.max(
    0,
    Number(response.resume?.next_sequence ?? 0),
    ...actualSegments.map((item) => item.sequence + 1),
    ...buffered.map((item) => item.sequence + 1),
    descriptor?.durableNextSequence || 0,
  );
  const durableTimelineMilliseconds = Math.max(
    0,
    descriptor?.durableTimelineMilliseconds || 0,
    ...buffered.map((item) => item.endedAtMs),
    ...actualSegments.map((item) => Number(item.ended_at_ms || 0)),
  );
  const completedTimelineMilliseconds = Math.max(
    durableTimelineMilliseconds,
    descriptor?.completedTimelineMilliseconds || 0,
  );
  const descriptorNext = Math.max(durableNextSequence, descriptor?.nextSequence || 0);
  const gaps = new Set<number>(response.gaps || []);
  for (let sequence = durableNextSequence; sequence < descriptorNext; sequence += 1) gaps.add(sequence);
  let nextSequence = descriptorNext;
  const timelineMilliseconds = Math.max(
    durableTimelineMilliseconds,
    descriptor?.timelineMilliseconds || 0,
  );
  if (timelineMilliseconds > completedTimelineMilliseconds) {
    gaps.add(nextSequence);
    nextSequence += 1;
  }
  nextSequence = Math.max(nextSequence, ...[...gaps].map((sequence) => sequence + 1));
  if (nextSequence > 0 && timelineMilliseconds <= 0) {
    throw new Error('voice.long_run.resume_cursor_invalid');
  }
  return {
    nextSequence,
    timelineMilliseconds,
    durableNextSequence,
    durableTimelineMilliseconds,
    completedTimelineMilliseconds,
    gaps: [...gaps].sort((left, right) => left - right),
  };
}

export function requestFromRun(run: VoiceLongRunState): VoiceLongRunCreateRequest {
  return {
    source: run.source === 'system_audio' ? 'system_audio' : 'microphone',
    profile_id: String(run.profile_id || 'default'),
    configuration_session_id: run.configuration_session_id || undefined,
    segment_duration_seconds: Number(run.segment_duration_seconds || 120),
    max_duration_seconds: Number(run.max_duration_seconds || 28_800),
    overlap_milliseconds: Number(run.overlap_milliseconds || 0),
  };
}

export function sameRecoveryRequest(
  metadata: VoiceLongRunRecoveryMetadata,
  hubUrl: string,
  request: VoiceLongRunCreateRequest,
): boolean {
  return metadata.hubUrl === hubUrl && JSON.stringify(metadata.request) === JSON.stringify(request);
}

export function validLeaseToken(lease: VoiceLongRunLease, profileId: string): string {
  const token = String(lease?.lease_token || '').trim();
  const expiresAt = timestampMilliseconds(lease?.expires_at);
  if (!token || lease?.profile_id !== profileId
    || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
    throw new Error('voice.long_run.start_lease_invalid');
  }
  return token;
}

export function captureDeadlineExpired(run: VoiceLongRunState): boolean {
  return timestampExpired(run.capture_deadline_at);
}

export function runExpired(run: VoiceLongRunState): boolean {
  return timestampExpired(run.expires_at);
}

export function terminalRun(run: VoiceLongRunState): boolean {
  return ['completed', 'completed_with_gaps', 'expired', 'failed', 'cancelled']
    .includes(run.status);
}

export function timestampExpired(raw: string | number | null | undefined): boolean {
  const milliseconds = timestampMilliseconds(raw);
  return Number.isFinite(milliseconds) && milliseconds <= Date.now();
}

export function timestampMilliseconds(raw: string | number | null | undefined): number {
  if (raw == null || raw === '') return Number.NaN;
  return typeof raw === 'number'
    ? (raw < 10_000_000_000 ? raw * 1_000 : raw)
    : Date.parse(raw);
}

/** Absolute capture deadline: Hub-provided, else derived from start time, else from the remaining budget. */
export function captureDeadlineAt(
  run: VoiceLongRunState,
  maxDurationSeconds: number,
  timelineMilliseconds: number,
): number {
  const explicit = timestampMilliseconds(run.capture_deadline_at);
  const started = timestampMilliseconds(run.started_at);
  const remaining = Math.max(0, maxDurationSeconds * 1_000 - timelineMilliseconds);
  return Number.isFinite(explicit)
    ? explicit
    : Number.isFinite(started)
      ? started + maxDurationSeconds * 1_000
      : Date.now() + remaining;
}

export function normalizedRunVersion(value: number | undefined): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
}

export function segmentIdempotencyKey(runId: string, sequence: number): string {
  return `voice-ui:long-run-segment:${runId}:${sequence}`;
}

export function isRetriable(error: unknown): boolean {
  const candidate = (error as any)?.error?.data?.error
    ?? (error as any)?.error?.error
    ?? (error as any)?.error
    ?? error;
  if (typeof candidate?.retriable === 'boolean') return candidate.retriable;
  const status = Number((error as any)?.status || candidate?.status || 0);
  return status === 0 || status === 408 || status === 425 || status === 429 || status >= 500;
}

export function isNotFound(error: unknown): boolean {
  return Number((error as any)?.status || (error as any)?.error?.status || 0) === 404;
}

export function isSegmentsInFlight(error: unknown): boolean {
  const candidate = (error as any)?.error?.data?.error
    ?? (error as any)?.error?.error
    ?? (error as any)?.error
    ?? error;
  return String(candidate?.code || '') === 'voice_live_run.segments_in_flight'
    && candidate?.retriable !== false;
}

export function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function withTimeout<T>(operation: Promise<T>, milliseconds: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error('voice.long_run.secure_spool_timeout')),
      milliseconds,
    );
    operation.then(
      (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    );
  });
}
