import { TestBed } from '@angular/core/testing';
import { signal } from '@angular/core';

import {
  CognitiveStyleSummaryComponent,
  CognitiveStyleUiStore,
} from './cognitive-style-summary.component';

describe('CognitiveStyleSummaryComponent', () => {
  it('shows continuous scores, role target, confidence and the permission boundary', async () => {
    const store = {
      readModel: signal<any>({
        schema: 'ananta.cognitive-style-read-model.v1',
        configuration: {
          schema: 'ananta.cognitive-style-configuration.v1', revision: 3,
          profiles: [{
            schema: 'ananta.agent-style-profile.v1',
            profile_id: 'style-review', model_profile_id: 'review-model',
            scores: {
              rule_correctness: .8,
              truth_exploration: .9,
              initiative_assertiveness: .5,
            },
            confidence: .85, sample_count: 48,
            benchmark_revision: 'behavior-style-v1',
            measured_at: '2026-08-25T00:00:00Z', source: 'measured',
            model_revision: 'r1', quantization: 'q8', runtime: 'llamacpp',
            backend_id: 'lmstudio', evidence_refs: ['style-observation://review'],
          }],
          role_targets: [{
            schema: 'ananta.role-style-target.v1',
            target_id: 'standard.reviewer.v1', role_id: 'reviewer',
            rule_correctness: { minimum: .65, maximum: 1, weight: 2 },
            truth_exploration: { minimum: .75, maximum: 1, weight: 3 },
            initiative_assertiveness: { minimum: .35, maximum: .8, weight: 1 },
            project_id: null, organization_id: null, overlay_id: null,
            rationale: 'Evidenz prüfen und Prämissen offenlegen.',
          }],
          overlays: [],
        },
        profile_history: [],
        heuristic_notice: 'Operative Heuristik.',
      }),
      loading: signal(false),
      error: signal<string | null>(null),
      load: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [CognitiveStyleSummaryComponent],
      providers: [{ provide: CognitiveStyleUiStore, useValue: store }],
    }).compileComponents();
    const fixture = TestBed.createComponent(CognitiveStyleSummaryComponent);
    fixture.componentRef.setInput('roleId', 'reviewer');
    fixture.componentRef.setInput('modelProfileId', 'review-model');
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Regel/Korrektheit');
    expect(text).toContain('Exploration');
    expect(text).toContain('Confidence 85%');
    expect(text).toContain('Rollenziel reviewer');
    expect(text).toContain('3/3 Zielbereiche getroffen');
    expect(text).toContain('nach allen harten Gates');
    expect(text).toContain('verleiht keine Rechte');
    expect(store.load).toHaveBeenCalledOnce();
  });

  it('keeps missing measurements explicit instead of inventing a classification', async () => {
    const store = {
      readModel: signal<any>({
        configuration: { profiles: [], role_targets: [], overlays: [] },
      }),
      loading: signal(false),
      error: signal<string | null>(null),
      load: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [CognitiveStyleSummaryComponent],
      providers: [{ provide: CognitiveStyleUiStore, useValue: store }],
    }).compileComponents();
    const fixture = TestBed.createComponent(CognitiveStyleSummaryComponent);
    fixture.componentRef.setInput('modelProfileId', 'unknown-model');
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain(
      'Kein gemessenes Profil für unknown-model zugeordnet.',
    );
  });
});
