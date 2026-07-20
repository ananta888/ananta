import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { SemanticSpeechPayload } from './semantic-speech-transport.service';

export type SpeechTranscriptDisplayState =
  | 'provisional' | 'final' | 'corrected' | 'correction_failed' | 'ordinary_fallback';

export type SpeechCorrectionStatus =
  | 'not_requested'
  | 'awaiting_source'
  | 'pending'
  | 'completed'
  | 'failed'
  | 'missing_source'
  | 'disabled';

export interface SpeechTranscriptTurn {
  turnId: string;
  revision: number;
  text: string;
  state: SpeechTranscriptDisplayState;
  originalCandidates: readonly { revision: number; text: string; authority: string }[];
  sourceDigest: string | null;
  correctionStatus: SpeechCorrectionStatus;
  correctionReason: string | null;
  playbackReason: string | null;
}

@Injectable({ providedIn: 'root' })
export class SpeechTranscriptRevisionStore {
  private readonly turns = new Map<string, SpeechTranscriptTurn>();
  readonly turns$ = new BehaviorSubject<readonly SpeechTranscriptTurn[]>([]);
  private liveMode = true;
  private ordinaryOverride = false;
  private ordinaryReason = 'ordinary_audio_override';
  private readonly ordinaryTurns = new Map<string, string>();

  setLiveMode(enabled: boolean): void {
    this.liveMode = Boolean(enabled);
    this.publish();
  }

  apply(payload: SemanticSpeechPayload): boolean {
    if (payload.kind !== 'transcript_revision' && payload.kind !== 'correction') return false;
    if (!payload.authority || typeof payload.text !== 'string') return false;
    const current = this.turns.get(payload.turn_id);
    if (current && payload.revision <= current.revision) return false;
    if (
      payload.kind === 'correction'
      && current?.sourceDigest
      && payload.source_digest !== current.sourceDigest
    ) return false;
    const candidate = { revision: payload.revision, text: payload.text, authority: payload.authority };
    const originals = [...(current?.originalCandidates ?? []), candidate];
    const state = this.displayState(payload.authority);
    const correction = this.correctionFor(payload, current);
    this.turns.set(payload.turn_id, {
      turnId: payload.turn_id,
      revision: payload.revision,
      text: payload.text,
      state,
      originalCandidates: Object.freeze(originals),
      sourceDigest: payload.source_digest,
      correctionStatus: correction.status,
      correctionReason: correction.reason,
      playbackReason: current?.playbackReason ?? null,
    });
    this.publish();
    return true;
  }

  ordinaryFallback(turnId: string, reasonText = ''): void {
    const current = this.turns.get(turnId);
    if (!current) return;
    this.ordinaryTurns.set(turnId, reasonText || 'ordinary_audio_fallback');
    this.publish();
  }

  setOrdinaryOverride(enabled: boolean, reason = 'ordinary_audio_override'): void {
    this.ordinaryOverride = Boolean(enabled);
    this.ordinaryReason = reason;
    this.publish();
  }

  markCorrection(
    turnId: string,
    expectedRevision: number,
    status: SpeechCorrectionStatus,
    reason: string | null,
  ): boolean {
    const current = this.turns.get(turnId);
    if (!current || current.revision !== expectedRevision || current.state === 'provisional') return false;
    this.turns.set(turnId, Object.freeze({
      ...current,
      correctionStatus: status,
      correctionReason: reason,
    }));
    this.publish();
    return true;
  }

  turn(turnId: string): SpeechTranscriptTurn | null {
    const value = this.turns.get(turnId);
    return value ? Object.freeze({ ...value }) : null;
  }

  clear(): void {
    this.turns.clear();
    this.ordinaryTurns.clear();
    this.ordinaryOverride = false;
    this.publish();
  }

  private publish(): void {
    const values = Array.from(this.turns.values())
      .filter(turn => this.liveMode || turn.state !== 'provisional')
      .map(turn => {
        const localReason = this.ordinaryTurns.get(turn.turnId);
        const ordinary = this.ordinaryOverride || Boolean(localReason);
        return Object.freeze({
          ...turn,
          state: ordinary ? 'ordinary_fallback' as const : turn.state,
          playbackReason: ordinary ? (localReason || this.ordinaryReason) : turn.playbackReason,
        });
      });
    this.turns$.next(Object.freeze(values));
  }

  private displayState(authority: string): SpeechTranscriptDisplayState {
    if (authority === 'corrected') return 'corrected';
    if (authority === 'correction_failed' || authority === 'missing_source') return 'correction_failed';
    return authority === 'provisional' ? 'provisional' : 'final';
  }

  private correctionFor(
    payload: SemanticSpeechPayload,
    current: SpeechTranscriptTurn | undefined,
  ): Readonly<{ status: SpeechCorrectionStatus; reason: string | null }> {
    if (payload.kind !== 'correction') {
      return Object.freeze({
        status: payload.authority === 'final' ? 'not_requested' : (current?.correctionStatus ?? 'not_requested'),
        reason: payload.authority === 'final' ? null : (current?.correctionReason ?? null),
      });
    }
    if (payload.authority === 'corrected') {
      return Object.freeze({ status: 'completed', reason: payload.reason_code || 'corrected' });
    }
    if (payload.authority === 'missing_source') {
      return Object.freeze({ status: 'missing_source', reason: payload.reason_code || 'source_missing_or_expired' });
    }
    return Object.freeze({ status: 'failed', reason: payload.reason_code || 'source_correction_failed' });
  }
}
