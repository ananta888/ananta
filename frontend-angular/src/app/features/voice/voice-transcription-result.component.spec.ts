import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';

import { VoiceTranscriptionResultComponent } from './voice-transcription-result.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoiceTranscriptionResultComponent', () => {
  it('renders immutable ASR text, model metadata and the generative correction diff', () => {
    const fixture = TestBed.createComponent(VoiceTranscriptionResultComponent);
    fixture.componentRef.setInput('fallbackBackend', 'vosk');
    fixture.componentRef.setInput('result', {
      text: 'Hallo Welt.',
      original_text: 'hallo weld',
      raw_backend: 'vosk',
      result_ref: 'voice-result-a',
      generative_corrector: {
        changed: true,
        review_required: true,
        original_text: 'hallo weld',
        corrected_text: 'Hallo Welt.',
        model_id: 'gemma-2b-it',
        model_revision: 'revision-a',
        edits: [{ operation: 'replace', before: 'weld', after: 'Welt.', reason: 'spelling' }],
      },
    });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';

    expect(text).toContain('hallo weld');
    expect(text).toContain('Hallo Welt.');
    expect(text).toContain('gemma-2b-it · revision-a');
    expect(text).toContain('weld → Welt.');
    expect(text).toContain('voice-result-a');
  });

  it('makes a fail-open correction and its reason visible', () => {
    const fixture = TestBed.createComponent(VoiceTranscriptionResultComponent);
    fixture.componentRef.setInput('result', {
      text: 'Version 42 bleibt erhalten.',
      original_text: 'Version 42 bleibt erhalten.',
      generative_corrector: {
        status: 'fallback',
        changed: false,
        review_required: true,
        reason_code: 'generative_corrector_protected_token_changed',
        original_text: 'Version 42 bleibt erhalten.',
        corrected_text: 'Version 42 bleibt erhalten.',
      },
    });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';

    expect(text).toContain('LLM-Fallback');
    expect(text).toContain('generative_corrector_protected_token_changed');
  });

  it('does not label the ASR model as a correction model when no correction ran', () => {
    const fixture = TestBed.createComponent(VoiceTranscriptionResultComponent);
    fixture.componentRef.setInput('result', {
      text: 'Nur ASR.',
      model: 'vosk-model-small-de-0.15',
      raw_backend: 'vosk',
    });
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';

    expect(text).toContain('keine generative Korrektur');
    expect(text).not.toContain('Korrekturmodellvosk-model-small-de-0.15');
  });
});
