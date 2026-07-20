import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';
import { SemanticSpeechPayload } from './semantic-speech-transport.service';
import { SpeechTranscriptRevisionStore } from './speech-transcript-revision.store';

function revision(revisionNumber: number, authority: SemanticSpeechPayload['authority'], text: string): SemanticSpeechPayload {
  return {
    version: 'ananta.semantic-speech.v1', kind: ['corrected', 'correction_failed', 'missing_source'].includes(String(authority))
      ? 'correction' : 'transcript_revision',
    session_id: 'session-a', epoch: 1, turn_id: 'turn-a', revision: revisionNumber,
    sender_id: 'alice', audience_id: 'bob', consent_version: 1, expires_at_ms: Date.now() + 1000,
    contract_digest: 'a'.repeat(64), source_digest: 'b'.repeat(64), authority, text,
  };
}

describe('SpeechTranscriptRevisionStore', () => {
  let store: SpeechTranscriptRevisionStore;
  beforeEach(() => { TestBed.resetTestingModule(); store = TestBed.inject(SpeechTranscriptRevisionStore); });

  it('keeps original candidates and rejects stale or duplicate revisions', () => {
    expect(store.apply(revision(1, 'provisional', 'Hal'))).toBe(true);
    expect(store.apply(revision(1, 'provisional', 'duplicate'))).toBe(false);
    expect(store.apply(revision(2, 'final', 'Hallo'))).toBe(true);
    expect(store.apply(revision(3, 'corrected', 'Hallo Welt'))).toBe(true);
    const turn = store.turns$.value[0];
    expect(turn.state).toBe('corrected');
    expect(turn.correctionStatus).toBe('completed');
    expect(turn.originalCandidates.map(value => value.text)).toEqual(['Hal', 'Hallo', 'Hallo Welt']);
  });

  it('hides partials in segment mode but retains them for final provenance', () => {
    store.setLiveMode(false);
    store.apply(revision(1, 'provisional', 'Hal'));
    expect(store.turns$.value).toHaveLength(0);
    store.apply(revision(2, 'final', 'Hallo'));
    expect(store.turns$.value[0].originalCandidates).toHaveLength(2);
  });

  it('keeps correction status/reason separate from a reversible ordinary-audio projection', () => {
    store.apply(revision(1, 'final', 'Final sichtbar'));
    expect(store.markCorrection('turn-a', 1, 'pending', 'source_correction_pending')).toBe(true);
    store.setOrdinaryOverride(true);
    expect(store.turns$.value[0]).toMatchObject({
      revision: 1,
      state: 'ordinary_fallback',
      correctionStatus: 'pending',
      correctionReason: 'source_correction_pending',
    });
    store.setOrdinaryOverride(false);
    expect(store.turns$.value[0]).toMatchObject({ revision: 1, state: 'final' });
  });

  it('rejects a correction whose source digest is not bound to the final segment', () => {
    store.apply(revision(1, 'final', 'Final'));
    expect(store.apply({
      ...revision(2, 'corrected', 'Nicht gebunden'), source_digest: 'c'.repeat(64),
    })).toBe(false);
    expect(store.turns$.value[0].text).toBe('Final');
  });
});
