import { VoiceLongRunDisplayMode } from './voice-long-run-display-mode';
import { VoiceLongRunRecoveryMetadata } from './voice-long-run-recovery';
import { VoiceLongRunObserver } from './voice-long-run.controller';
import {
  VoiceLongRunTimelineSegment,
  voiceLongRunRevisionLabel,
  voiceLongRunSegmentIsGap,
} from './voice-long-run-timeline';
import { voiceError } from './voice-ui.helpers';
import { VoiceLongRunResponse } from './voice.models';

/**
 * Console-side projection of long-run capture: the upload ledger, the
 * controller-event observer that updates the console's long-run view state
 * and pure timeline/format helpers. The long-run controller stays the owner
 * of capture, spool and Hub traffic.
 */

export interface VoiceLongRunTimelineRow {
  kind: 'segment' | 'gap';
  sequence: number;
  text: string;
  stateLabel: string;
  textState: string;
}

/** Long-run view fields of the console that controller events update. */
export interface VoiceConsoleLongRunView {
  longRunActive: boolean;
  longRunBusy: boolean;
  longRunId: string;
  longRunStatus: string;
  longRunDisplayMode: VoiceLongRunDisplayMode;
  longRunPreviewSequence: number;
  longRunPreviewText: string;
  longRunPreviewStatus: 'idle' | 'connecting' | 'live' | 'unavailable';
  longRunCapturedMilliseconds: number;
  longRunUploadedSegments: number;
  longRunQueuedSegments: number;
  longRunGapSequences: number[];
  longRunTranscript: string;
  longRunTimeline: readonly VoiceLongRunTimelineSegment[];
  longRunProvisionalSegments: number;
  longRunCorrectedSegments: number;
  longRunConnection: 'online' | 'retrying';
  longRunRecovery: VoiceLongRunRecoveryMetadata | null;
  longRunWarning: string;
  errorCode: string;
  errorMessage: string;
  successMessage: string;
}

/** Console hooks the observer needs besides plain view fields. */
export interface VoiceConsoleLongRunHooks {
  readonly destroyed: () => boolean;
  readonly changed: () => void;
  readonly applyResponse: (response: VoiceLongRunResponse) => void;
  readonly rebuildRows: () => void;
  readonly restoreDisplayMode: (metadata: VoiceLongRunRecoveryMetadata | null) => void;
}

/** Hub-confirmed segment sequences and the newest run version seen by the console. */
export class VoiceLongRunUploadLedger {
  private readonly confirmed = new Set<number>();
  private latestVersion: number | null = null;

  get confirmedCount(): number { return this.confirmed.size; }

  reset(): void {
    this.confirmed.clear();
    this.latestVersion = null;
  }

  confirm(sequence: number): void { this.confirmed.add(sequence); }

  /** Records the run version; false for a stale projection older than one already applied. */
  acceptVersion(value: number | undefined): boolean {
    const version = normalizedLongRunVersion(value);
    const currentProjection = !(
      (version == null && this.latestVersion != null)
      || (version != null && this.latestVersion != null && version < this.latestVersion)
    );
    if (currentProjection && version != null) this.latestVersion = version;
    return currentProjection;
  }
}

export function applyVoiceLongRunResponse(
  view: VoiceConsoleLongRunView,
  ledger: VoiceLongRunUploadLedger,
  response: VoiceLongRunResponse,
): void {
  if (ledger.acceptVersion(response.run.version)) {
    view.longRunId = response.run.id;
    view.longRunStatus = response.run.status;
  }
  const authoritative = String(response.composed_transcript || '').trim();
  if (!view.longRunTimeline.length && authoritative) view.longRunTranscript = authoritative;
  const acknowledged = Number(response.resume?.acknowledged_through_sequence ?? -1);
  if (Number.isInteger(acknowledged) && acknowledged >= 0) {
    for (let sequence = 0; sequence <= acknowledged; sequence += 1) {
      ledger.confirm(sequence);
    }
  }
  const upload = response as VoiceLongRunResponse & {
    segment?: { sequence: number; status: string };
  };
  for (const segment of [...(response.segments || []), ...(upload.segment ? [upload.segment] : [])]) {
    if (segment.status === 'completed') ledger.confirm(segment.sequence);
  }
  view.longRunUploadedSegments = ledger.confirmedCount;
}

export function createVoiceConsoleLongRunObserver(
  view: VoiceConsoleLongRunView,
  ledger: VoiceLongRunUploadLedger,
  hooks: VoiceConsoleLongRunHooks,
): VoiceLongRunObserver {
  const { destroyed, changed } = hooks;
  return {
    timelineUpdated: (snapshot) => {
      if (destroyed()) return;
      view.longRunTimeline = snapshot.segments;
      view.longRunTranscript = snapshot.composedTranscript;
      view.longRunProvisionalSegments = snapshot.segments
        .filter((segment) => segment.text_state === 'provisional').length;
      view.longRunCorrectedSegments = snapshot.segments
        .filter((segment) => segment.correction_status === 'completed').length;
      for (const segment of snapshot.segments) {
        if (segment.status === 'completed') ledger.confirm(segment.sequence);
      }
      if (snapshot.segments.some((segment) => (
        segment.sequence === view.longRunPreviewSequence
        && segment.text_state !== 'none'
        && Boolean(segment.text)
      ))) {
        view.longRunPreviewSequence = -1;
        view.longRunPreviewText = '';
        view.longRunPreviewStatus = 'connecting';
      }
      view.longRunUploadedSegments = ledger.confirmedCount;
      hooks.rebuildRows();
      changed();
    },
    runUpdated: (response) => {
      if (destroyed()) return;
      hooks.applyResponse(response);
    },
    progress: (milliseconds) => {
      if (destroyed()) return;
      view.longRunCapturedMilliseconds = milliseconds;
      changed();
    },
    buffered: (_metadata, queued) => {
      if (destroyed()) return;
      view.longRunQueuedSegments = queued;
      changed();
    },
    segmentUploaded: (response, queued) => {
      if (destroyed()) return;
      view.longRunQueuedSegments = queued;
      hooks.applyResponse(response);
    },
    segmentFailed: (sequence) => {
      if (destroyed()) return;
      view.longRunWarning = `Segment ${sequence + 1} konnte nicht verarbeitet werden und wurde als Lücke markiert.`;
      changed();
    },
    gap: (sequence) => {
      if (destroyed() || view.longRunGapSequences.includes(sequence)) return;
      view.longRunGapSequences = [...view.longRunGapSequences, sequence].sort((left, right) => left - right);
      view.longRunWarning = 'Der verschlüsselte Offline-Puffer war ausgelastet. Nicht bestätigte Segmente sind als Lücke markiert.';
      hooks.rebuildRows();
      changed();
    },
    gapsUpdated: (sequences) => {
      if (destroyed()) return;
      const hadGaps = view.longRunGapSequences.length > 0;
      view.longRunGapSequences = [...sequences];
      if (hadGaps && !view.longRunGapSequences.length && isLongRunGapWarning(view.longRunWarning)) {
        view.longRunWarning = '';
      }
      hooks.rebuildRows();
      changed();
    },
    recoveryUpdated: (metadata) => {
      if (destroyed()) return;
      view.longRunRecovery = { ...metadata };
      hooks.restoreDisplayMode(view.longRunRecovery);
      changed();
    },
    livePreviewStarted: (segmentSequence) => {
      if (destroyed() || view.longRunDisplayMode !== 'live'
        || hasAuthoritativeLongRunText(view.longRunTimeline, segmentSequence)) return;
      view.longRunPreviewSequence = segmentSequence;
      view.longRunPreviewText = '';
      view.longRunPreviewStatus = 'connecting';
      changed();
    },
    livePreview: (update) => {
      if (destroyed() || view.longRunDisplayMode !== 'live'
        || hasAuthoritativeLongRunText(view.longRunTimeline, update.segmentSequence)) return;
      view.longRunPreviewSequence = update.segmentSequence;
      view.longRunPreviewText = update.text;
      view.longRunPreviewStatus = 'live';
      changed();
    },
    livePreviewUnavailable: () => {
      if (destroyed() || view.longRunDisplayMode !== 'live') return;
      view.longRunPreviewStatus = 'unavailable';
      view.longRunPreviewText = '';
      view.longRunWarning = 'Die flüchtige Live-Vorschau ist nicht verfügbar. Aufnahme, verschlüsselter Puffer, Segment-ASR und Korrektur laufen weiter.';
      changed();
    },
    connection: (state) => {
      if (destroyed()) return;
      view.longRunConnection = state;
      changed();
    },
    stopping: (reason) => {
      if (destroyed()) return;
      view.longRunBusy = true;
      view.longRunStatus = reason === 'safety_limit' ? '8-Stunden-Limit erreicht' : 'wird abgeschlossen';
      changed();
    },
    stopped: (response, reason) => {
      if (destroyed()) return;
      hooks.applyResponse(response);
      view.longRunActive = false;
      view.longRunBusy = false;
      view.longRunRecovery = null;
      view.longRunPreviewSequence = -1;
      view.longRunPreviewText = '';
      view.longRunPreviewStatus = 'idle';
      view.successMessage = reason === 'safety_limit'
        ? 'Das konfigurierte Langzeit-Limit wurde erreicht und der Run automatisch abgeschlossen.'
        : 'Langzeit-Run abgeschlossen.';
      changed();
    },
    error: (error) => {
      if (destroyed()) return;
      const detail = voiceError(error);
      view.errorCode = detail.code;
      view.errorMessage = detail.message;
      changed();
    },
  };
}

export function buildVoiceLongRunTimelineRows(
  timeline: readonly VoiceLongRunTimelineSegment[],
  gapSequences: readonly number[],
): VoiceLongRunTimelineRow[] {
  const rows = timeline.map((segment): VoiceLongRunTimelineRow => {
    const isGap = voiceLongRunSegmentIsGap(segment);
    return {
      kind: isGap ? 'gap' : 'segment',
      sequence: segment.sequence,
      text: isGap
        ? 'Nicht wiederherstellbare Segmentlücke'
        : segment.display_text || (segment.text_state === 'none' ? 'Segment wird transkribiert …' : ''),
      stateLabel: voiceLongRunRevisionLabel(segment),
      textState: isGap ? 'gap' : segment.text_state,
    };
  });
  const present = new Set(rows.map((row) => row.sequence));
  for (const sequence of gapSequences) {
    if (present.has(sequence)) continue;
    rows.push({
      kind: 'gap',
      sequence,
      text: 'Nicht wiederherstellbare Segmentlücke',
      stateLabel: 'Lücke',
      textState: 'gap',
    });
  }
  return rows.sort((left, right) => left.sequence - right.sequence);
}

export function hasAuthoritativeLongRunText(
  timeline: readonly VoiceLongRunTimelineSegment[],
  sequence: number,
): boolean {
  return timeline.some((segment) => (
    segment.sequence === sequence
    && segment.text_state !== 'none'
    && Boolean(String(segment.text || '').trim())
  ));
}

export function isLongRunGapWarning(warning: string): boolean {
  return warning.startsWith('Der verschlüsselte Offline-Puffer')
    || warning.startsWith('Segment ');
}

export function normalizedLongRunVersion(value: number | undefined): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
}

export function validLongRunSegmentSeconds(value: unknown): number {
  const rounded = Math.round(Number(value));
  return [60, 90, 120].includes(rounded) ? rounded : 120;
}

export function validLongRunMaxHours(value: unknown): number {
  const rounded = Math.round(Number(value));
  return [1, 2, 4, 8].includes(rounded) ? rounded : 8;
}

export function formatVoiceLongRunDuration(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor((totalSeconds % 3_600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':');
}
