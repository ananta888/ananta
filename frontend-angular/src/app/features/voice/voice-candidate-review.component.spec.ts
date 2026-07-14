import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { of } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceCandidateReviewComponent, VoiceLearningContext } from './voice-candidate-review.component';
import { VoiceReview, VoiceTranscriptionResult } from './voice.models';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoiceCandidateReviewComponent', () => {
  const result: VoiceTranscriptionResult = {
    audit_id: 'audit-voice-a',
    text: 'Ananta baut',
    selected_candidate_id: 'candidate-a',
    fusion_strategy: 'deterministic_consensus',
    provenance_valid: true,
    candidates: [
      {
        candidate_id: 'candidate-a', backend: 'whisper_cpp', model: 'small', model_revision: 'rev-a',
        device: 'cpu', execution_location: 'voice-runtime', audio_variant_id: 'original',
        source_audio_digest: 'sha256:audio-a', lineage_id: 'lineage-a',
        text: 'Ananta baut', confidence: .91, status: 'succeeded',
        parent_candidate_ids: [], segments: [{ start_ms: 0, end_ms: 800, text: 'Ananta baut', confidence: .91 }],
      },
      {
        candidate_id: 'candidate-b', backend: 'vosk', model: 'de', model_revision: 'rev-b',
        device: 'cpu', execution_location: 'restricted-inference-worker', audio_variant_id: 'denoised',
        source_audio_digest: 'sha256:audio-b', lineage_id: 'lineage-b',
        text: 'Ananta Bau', confidence: .73, status: 'succeeded',
        parent_candidate_ids: ['candidate-raw'], segments: [{ start_ms: 0, end_ms: 820, text: 'Ananta Bau', confidence: .73 }],
      },
    ],
    disagreement_regions: [{
      region_id: 'region-1', start_ms: 500, end_ms: 820, selected_candidate_id: 'candidate-a',
      alternatives: [
        { candidate_id: 'candidate-a', text: 'baut' },
        { candidate_id: 'candidate-b', text: 'Bau' },
      ],
    }],
  };
  const pending: VoiceReview = {
    id: 'review-a', profile_id: 'default', result_ref: 'audit-voice-a',
    candidate_ids: ['candidate-a', 'candidate-b'], state: 'pending', version: 1,
  };
  const api = {
    transcribe: vi.fn(() => of(result)),
    createReview: vi.fn(() => of(pending)),
    decideReview: vi.fn(() => of({ ...pending, state: 'accepted', selected_candidate_id: 'candidate-a', version: 2 })),
    addPersonalizationFeedback: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [VoiceCandidateReviewComponent],
      providers: [{ provide: VoiceApiService, useValue: api }],
    });
  });

  it('renders synchronized candidates, provenance, lineage and disagreements', () => {
    const fixture = TestBed.createComponent(VoiceCandidateReviewComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentInstance.file = new File(['audio'], 'sample.wav', { type: 'audio/wav' });
    fixture.componentInstance.transcribe();
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelectorAll('.voice-candidate')).toHaveLength(2);
    expect(element.textContent).toContain('whisper_cpp');
    expect(element.textContent).toContain('candidate-raw');
    expect(element.textContent).toContain('lineage-a');
    expect(element.textContent).toContain('sha256:audio-a');
    expect(element.textContent).toContain('restricted-inference-worker');
    expect(element.textContent).toContain('Quelle: vosk · lineage lineage-b · audio denoised · sha256:audio-b');
    expect(element.textContent).toContain('0.50 s–0.82 s');
    expect(element.textContent).toContain('Ananta Bau');
  });

  it('routes accept through Hub review and never enables learning implicitly', () => {
    const fixture = TestBed.createComponent(VoiceCandidateReviewComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    const component = fixture.componentInstance;
    const contexts: Array<VoiceLearningContext | null> = [];
    component.learningContextChange.subscribe((context) => contexts.push(context));
    component.file = new File(['audio'], 'sample.wav', { type: 'audio/wav' });
    component.transcribe();
    component.createReview();
    component.decide('accept');

    expect(api.createReview).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ result_ref: 'audit-voice-a', candidate_ids: ['candidate-a', 'candidate-b'] }),
      expect.any(String),
    );
    expect(api.decideReview).toHaveBeenCalledWith(
      'http://hub.test', 'review-a',
      expect.objectContaining({ decision: 'accept', expected_version: 1, selected_candidate_id: 'candidate-a' }),
      expect.any(String),
    );
    expect(api.addPersonalizationFeedback).not.toHaveBeenCalled();
    expect(contexts.at(-1)?.review.state).toBe('accepted');
  });

  it('keeps original candidates visible after a correction decision', () => {
    api.decideReview.mockReturnValueOnce(of({
      ...pending, state: 'corrected', selected_candidate_id: 'candidate-b', correction_text: 'Ananta baut', version: 2,
    }));
    const fixture = TestBed.createComponent(VoiceCandidateReviewComponent);
    const component = fixture.componentInstance;
    component.hubUrl = 'http://hub.test';
    component.result = result;
    component.review = pending;
    component.selectedCandidateId = 'candidate-b';
    component.correctionText = 'Ananta baut';
    component.decide('correct');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.voice-candidate')).toHaveLength(2);
    expect(component.review?.state).toBe('corrected');
  });

  it('reuses a console result and prepares the generative proposal for explicit review', () => {
    const fixture = TestBed.createComponent(VoiceCandidateReviewComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('hideTranscriptionInput', true);
    fixture.componentRef.setInput('embeddedProfileId', 'voice-profile');
    fixture.componentRef.setInput('embeddedSessionId', 'voice-session');
    fixture.componentRef.setInput('embeddedResult', {
      ...result,
      original_text: 'ananta baut',
      text: 'Ananta baut.',
      generative_corrector: {
        original_text: 'ananta baut', corrected_text: 'Ananta baut.', changed: true, review_required: true,
      },
    });
    fixture.detectChanges();

    expect(fixture.componentInstance.profileId).toBe('voice-profile');
    expect(fixture.componentInstance.sessionId).toBe('voice-session');
    expect(fixture.componentInstance.correctionText).toBe('Ananta baut.');
    expect((fixture.nativeElement as HTMLElement).querySelector('input[type="file"]')).toBeNull();
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.voice-candidate')).toHaveLength(2);
  });
});
