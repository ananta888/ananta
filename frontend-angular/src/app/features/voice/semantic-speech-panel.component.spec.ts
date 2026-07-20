import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { SpeechTranscriptRevisionStore } from '../../services/speech-transcript-revision.store';
import { SemanticSpeechPayload } from '../../services/semantic-speech-transport.service';
import { SemanticSpeechPanelComponent } from './semantic-speech-panel.component';

function revision(authority: SemanticSpeechPayload['authority'], revisionNumber: number): SemanticSpeechPayload {
  return {
    version: 'ananta.semantic-speech.v1', kind: ['corrected', 'correction_failed', 'missing_source'].includes(String(authority))
      ? 'correction' : 'transcript_revision',
    session_id: 'session-a', epoch: 1, turn_id: 'turn-a', revision: revisionNumber,
    sender_id: 'alice', audience_id: 'bob', consent_version: 1,
    expires_at_ms: Date.now() + 60_000, contract_digest: 'a'.repeat(64), source_digest: 'b'.repeat(64),
    authority, text: authority === 'corrected' ? 'Korrigierter Text' : 'Live Text',
  };
}

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('SemanticSpeechPanelComponent', () => {
  let fixture: ComponentFixture<SemanticSpeechPanelComponent>;
  let store: SpeechTranscriptRevisionStore;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      imports: [SemanticSpeechPanelComponent],
    });
    await TestBed.compileComponents();
    fixture = TestBed.createComponent(SemanticSpeechPanelComponent);
    store = TestBed.inject(SpeechTranscriptRevisionStore);
    fixture.detectChanges();
  });

  it('shows a live partial immediately and preserves correction provenance', () => {
    store.apply(revision('provisional', 1));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Live Text');
    expect(fixture.nativeElement.textContent).toContain('vorläufig');

    store.apply(revision('corrected', 2));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Korrigierter Text');
    expect(fixture.nativeElement.textContent).toContain('korrigiert');
    expect(store.turns$.value[0]?.originalCandidates).toHaveLength(2);
  });

  it('hides partials in segment mode independently from segment duration and correction', () => {
    const changes: unknown[] = [];
    fixture.componentInstance.settingsChange.subscribe(value => changes.push(value));
    store.apply(revision('provisional', 1));
    fixture.componentInstance.setDisplayMode('segment');
    fixture.componentInstance.setSegmentDuration(30);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('Live Text');
    expect(fixture.componentInstance.correctEachSegment).toBe(true);
    expect(changes.at(-1)).toMatchObject({ displayMode: 'segment', segmentDurationSeconds: 30 });
  });

  it('exposes keyboard-native controls and truthful fallback/correction failure labels', () => {
    store.apply(revision('final', 1));
    store.apply({ ...revision('correction_failed', 2), reason_code: 'source_alignment_failed' });
    fixture.componentInstance.toggleOrdinaryAudio(true);
    fixture.componentInstance.togglePaused();
    fixture.componentRef.setInput('qualityMode', 'ordinary_audio');
    fixture.componentRef.setInput('qualityReason', 'speech_queue_high');
    fixture.detectChanges();

    const controls = fixture.nativeElement.querySelectorAll('button, select, input');
    expect(controls.length).toBeGreaterThanOrEqual(5);
    expect(fixture.nativeElement.textContent).toContain('Korrektur fehlgeschlagen');
    expect(fixture.nativeElement.textContent).toContain('Das finale Transkript bleibt sichtbar');
    expect(fixture.nativeElement.textContent).toContain('source_alignment_failed');
    expect(fixture.nativeElement.querySelector('[data-correction-status="failed"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('button[aria-pressed]').getAttribute('aria-pressed')).toBe('true');
    expect(fixture.nativeElement.querySelector('[data-testid="semantic-speech-quality-status"]').textContent)
      .toContain('speech_queue_high');
  });

  it('emits start and stop lifecycle intents only from valid transport states', () => {
    const starts = vi.fn();
    const stops = vi.fn();
    fixture.componentInstance.startRequested.subscribe(starts);
    fixture.componentInstance.stopRequested.subscribe(stops);

    fixture.componentInstance.canStartTransport = true;
    fixture.componentInstance.transportState = 'stopped';
    fixture.componentInstance.requestStart();
    fixture.componentInstance.requestStop();
    expect(starts).toHaveBeenCalledOnce();
    expect(stops).not.toHaveBeenCalled();

    fixture.componentInstance.transportState = 'active';
    fixture.componentInstance.requestStart();
    fixture.componentInstance.requestStop();
    expect(starts).toHaveBeenCalledOnce();
    expect(stops).toHaveBeenCalledOnce();
  });

  it('keeps every speech setting keyboard-native and labelled in a narrow host', () => {
    fixture.nativeElement.style.width = '320px';
    fixture.detectChanges();
    const ids = [
      'semantic-speech-display-mode',
      'semantic-speech-segment-duration',
      'semantic-speech-correct-each-segment',
      'semantic-speech-ordinary-override',
      'semantic-speech-pause',
    ];
    for (const id of ids) {
      const control = fixture.nativeElement.querySelector(`[data-testid="${id}"]`) as HTMLElement;
      expect(control).not.toBeNull();
      expect(['BUTTON', 'INPUT', 'SELECT']).toContain(control.tagName);
      if (control.tagName !== 'BUTTON') expect(control.closest('label')?.textContent?.trim()).toBeTruthy();
    }
    expect(fixture.nativeElement.querySelector('fieldset legend')?.textContent).toContain('Anzeige');
    expect(fixture.nativeElement.querySelector('.transcript[aria-live="polite"]')).not.toBeNull();
  });
});
