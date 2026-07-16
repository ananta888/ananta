import {
  VoiceLongRunCorrectionStatus,
  VoiceLongRunResponse,
  VoiceLongRunSegment,
  VoiceLongRunTextState,
} from './voice.models';

const PENDING_SEGMENT_STATES = new Set(['pending', 'processing']);
const PENDING_CORRECTION_STATES = new Set(['queued', 'pending', 'processing']);

export interface VoiceLongRunTimelineSegment extends VoiceLongRunSegment {
  revision: number;
  timeline_revision: number;
  text_state: VoiceLongRunTextState;
  correction_status: VoiceLongRunCorrectionStatus;
  text: string;
  /** Text contribution after removing the bounded overlap with prior segments. */
  display_text: string;
}

export interface VoiceLongRunTimelineSnapshot {
  segments: readonly VoiceLongRunTimelineSegment[];
  composedTranscript: string;
  highestTimelineRevision: number;
  hasPendingRevisions: boolean;
}

/**
 * Ordered client-side projection of the Hub's revision stream.
 *
 * Sequence controls position while revision controls replacement. This keeps a
 * delayed provisional response from rolling a corrected segment back.
 */
export class VoiceLongRunTimeline {
  private readonly bySequence = new Map<number, VoiceLongRunTimelineSegment>();
  private fallbackTranscript = '';
  private fallbackRevision = 0;

  reset(): void {
    this.bySequence.clear();
    this.fallbackTranscript = '';
    this.fallbackRevision = 0;
  }

  apply(response: VoiceLongRunResponse): VoiceLongRunTimelineSnapshot {
    const upload = response as VoiceLongRunResponse & {
      segment?: VoiceLongRunSegment;
      result?: { transcript?: string; text?: string };
    };
    const incoming = (response.segments || []).filter((segment) => segment.status !== 'gap');
    if (upload.segment && upload.segment.status !== 'gap') {
      const resultText = String(upload.result?.transcript || upload.result?.text || '').trim();
      incoming.push(upload.segment.text || !resultText
        ? upload.segment
        : { ...upload.segment, text: resultText });
    }
    for (const segment of incoming) this.merge(segment);
    const responseRevision = nonNegativeInteger(response.run.timeline_revision);
    if (response.composed_transcript != null && responseRevision >= this.fallbackRevision) {
      this.fallbackTranscript = String(response.composed_transcript).trim();
      this.fallbackRevision = responseRevision;
    }
    return this.snapshot();
  }

  snapshot(): VoiceLongRunTimelineSnapshot {
    const ordered = [...this.bySequence.values()].sort((left, right) => left.sequence - right.sequence);
    let composedTranscript = '';
    let highestTimelineRevision = 0;
    const segments = ordered.map((segment, index) => {
      const previous = index > 0 ? ordered[index - 1] : null;
      const overlap = timelineOverlap(previous, segment);
      const displayText = transcriptContribution(composedTranscript, segment.text, overlap ? 32 : 0);
      composedTranscript = appendTranscript(composedTranscript, segment.text, overlap ? 32 : 0);
      highestTimelineRevision = Math.max(highestTimelineRevision, segment.timeline_revision);
      return { ...segment, display_text: displayText };
    });
    return {
      segments,
      composedTranscript: composedTranscript || this.fallbackTranscript,
      highestTimelineRevision,
      hasPendingRevisions: segments.some(voiceLongRunSegmentIsPending),
    };
  }

  private merge(raw: VoiceLongRunSegment): void {
    const incoming = normalizeSegment(raw);
    const current = this.bySequence.get(incoming.sequence);
    // Metadata-only responses (notably heartbeat include_text=false) are not
    // content revisions. Advancing here would skip the later text-bearing
    // delta with the same run-wide cursor.
    if (incoming.revision > 0 && incoming.text_state !== 'none' && !incoming.text) return;
    if (!current) {
      this.bySequence.set(incoming.sequence, incoming);
      return;
    }
    if (incoming.revision < current.revision) return;
    if (incoming.revision === current.revision) {
      // Same-revision responses are idempotent. Keeping the existing
      // projection wholesale prevents a delayed upload response from rolling
      // final state metadata back to provisional.
      this.bySequence.set(incoming.sequence, {
        ...incoming,
        ...current,
        timeline_revision: Math.max(current.timeline_revision, incoming.timeline_revision),
      });
      return;
    }
    this.bySequence.set(incoming.sequence, incoming);
  }
}

export function voiceLongRunSegmentIsPending(segment: VoiceLongRunTimelineSegment): boolean {
  return PENDING_SEGMENT_STATES.has(segment.status)
    || PENDING_CORRECTION_STATES.has(segment.correction_status);
}

export function voiceLongRunSegmentIsGap(segment: VoiceLongRunTimelineSegment): boolean {
  return segment.status === 'failed';
}

export function voiceLongRunRevisionLabel(segment: VoiceLongRunTimelineSegment): string {
  if (voiceLongRunSegmentIsGap(segment)) return 'Lücke';
  if (segment.text_state === 'none') return 'wird transkribiert';
  if (segment.text_state === 'provisional') return 'vorläufig';
  if (segment.correction_status === 'completed') return 'korrigiert';
  if (segment.correction_status === 'failed') {
    const reason = voiceLongRunCorrectionFailureLabel(segment.correction_failure_code);
    return reason ? `final · Korrektur fehlgeschlagen: ${reason}` : 'final · Korrektur fehlgeschlagen';
  }
  if (segment.correction_status === 'skipped' || segment.text_state === 'final_uncorrected') {
    return 'final · unkorrigiert';
  }
  return 'final';
}

export function voiceLongRunCorrectionFailureLabel(code: string | null | undefined): string {
  const normalized = String(code || '').trim().toLowerCase();
  if (!normalized) return '';
  const labels: Record<string, string> = {
    corrector_engine_failed: 'Korrekturmodell fehlgeschlagen',
    corrector_provider_output_invalid: 'Korrekturmodell lieferte eine ungültige Ausgabe',
    corrector_provider_response_invalid: 'Korrektur-Provider lieferte eine ungültige Antwort',
    corrector_provider_http_error: 'Korrektur-Provider meldete einen HTTP-Fehler',
    corrector_provider_unavailable: 'Korrektur-Provider nicht verfügbar',
    corrector_provider_timeout: 'Zeitlimit des Korrektur-Providers überschritten',
    corrector_provider_request_failed: 'Anfrage an Korrektur-Provider fehlgeschlagen',
    correction_execution_failed: 'Korrekturausführung fehlgeschlagen',
    correction_execution_timeout: 'Zeitlimit der Korrekturausführung überschritten',
    correction_lease_expired: 'Zeitlimit der Korrektur überschritten',
    correction_voicelivecorrectionexecutionerror: 'Korrekturausführung fehlgeschlagen',
    correction_worker_unavailable: 'Korrektur-Worker nicht verfügbar',
    corrector_unavailable: 'Korrekturmodell nicht verfügbar',
    run_expired: 'Langzeit-Run abgelaufen',
  };
  return labels[normalized] || `technischer Grund: ${normalized}`;
}

/** Bounded word-overlap merge for rolling audio segments. */
export function appendVoiceLongRunTranscript(existing: string, next: string): string {
  return appendTranscript(existing, next, 50);
}

function appendTranscript(existing: string, next: string, maximumOverlap: number): string {
  const left = existing.trim();
  const right = next.trim();
  if (!left) return right;
  if (!right || left === right) return left;
  const leftWords = left.split(/\s+/);
  const rightWords = right.split(/\s+/);
  const overlap = transcriptOverlap(leftWords, rightWords, maximumOverlap);
  return [...leftWords, ...rightWords.slice(overlap)].join(' ').trim();
}

function transcriptContribution(existing: string, next: string, maximumOverlap: number): string {
  const right = next.trim();
  if (!right) return '';
  const leftWords = existing.trim() ? existing.trim().split(/\s+/) : [];
  const rightWords = right.split(/\s+/);
  return rightWords.slice(transcriptOverlap(leftWords, rightWords, maximumOverlap)).join(' ').trim();
}

function transcriptOverlap(leftWords: string[], rightWords: string[], limit: number): number {
  const maximum = Math.min(limit, leftWords.length, rightWords.length);
  for (let width = maximum; width > 0; width -= 1) {
    const suffix = leftWords.slice(-width).map(normalizedTranscriptWord);
    const prefix = rightWords.slice(0, width).map(normalizedTranscriptWord);
    if (suffix.some(Boolean) && suffix.every((word, index) => word === prefix[index])) return width;
  }
  return 0;
}

function timelineOverlap(
  previous: VoiceLongRunTimelineSegment | null,
  current: VoiceLongRunTimelineSegment,
): boolean {
  if (!previous) return false;
  const previousEnd = Number(previous.ended_at_ms);
  const currentStart = Number(current.started_at_ms);
  if (Number.isFinite(previousEnd) && Number.isFinite(currentStart)) return currentStart < previousEnd;
  return Number(current.overlap_milliseconds || 0) > 0;
}

function normalizeSegment(segment: VoiceLongRunSegment): VoiceLongRunTimelineSegment {
  const text = String(segment.text || '').trim();
  const textState = normalizeTextState(segment, text);
  return {
    ...segment,
    revision: normalizedRevision(segment.revision ?? segment.text_revision, textState, text),
    timeline_revision: nonNegativeInteger(segment.timeline_revision),
    text_state: textState,
    correction_status: normalizeCorrectionStatus(segment.correction_status, textState),
    text,
    display_text: text,
  };
}

function normalizeTextState(segment: VoiceLongRunSegment, text: string): VoiceLongRunTextState {
  if (['none', 'provisional', 'final', 'final_uncorrected'].includes(String(segment.text_state))) {
    return segment.text_state!;
  }
  if (!text) return 'none';
  if (PENDING_CORRECTION_STATES.has(String(segment.correction_status))) return 'provisional';
  return segment.status === 'pending' || segment.status === 'processing' ? 'provisional' : 'final';
}

function normalizeCorrectionStatus(
  status: VoiceLongRunCorrectionStatus | undefined,
  textState: VoiceLongRunTextState,
): VoiceLongRunCorrectionStatus {
  return status || (textState === 'provisional' ? 'pending' : 'not_requested');
}

function normalizedRevision(
  revision: number | undefined,
  textState: VoiceLongRunTextState,
  text: string,
): number {
  const explicit = nonNegativeInteger(revision);
  if (revision != null && Number.isFinite(Number(revision))) return explicit;
  if (!text || textState === 'none') return 0;
  return textState === 'provisional' ? 1 : 2;
}

function nonNegativeInteger(value: number | undefined): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? Math.max(0, Math.trunc(numeric)) : 0;
}

function normalizedTranscriptWord(value: string): string {
  return value.toLocaleLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
}
