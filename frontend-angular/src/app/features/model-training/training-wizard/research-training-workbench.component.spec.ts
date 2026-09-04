import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ModelTrainingApiService } from '../model-training-api.service';
import { ResearchTrainingWorkbenchComponent } from './research-training-workbench.component';

describe('ResearchTrainingWorkbenchComponent', () => {
  let fixture: ComponentFixture<ResearchTrainingWorkbenchComponent>;
  const api = {
    resolveResearchRecipe: vi.fn(),
    dryRunResearchTraining: vi.fn(),
    createResearchTraining: vi.fn(),
    getResearchRun: vi.fn(() => of({
      run_id: 'run-1', state: 'running', revision: 2, reason_code: 'research_stage_running',
      stages: {
        tokenizer: {
          stage_id: 'tokenizer', kind: 'tokenizer_train', dependencies: [], required_capability: 'tokenizer_training',
          max_attempts: 2, timeout_seconds: 60, status: 'completed', attempts: 1, output_artifact_digest: 'a'.repeat(64),
        },
      },
    })),
    getResearchLineage: vi.fn(() => of({
      items: [{
        artifact_digest: 'a'.repeat(64), artifact_ref: 'opaque/ref',
        manifest: { artifact_kind: 'tokenizer', parent_artifact_digests: [] },
      }],
      limit: 100,
    })),
    getResearchMetrics: vi.fn(() => of({
      schema: 'ananta.research-training-metric-list.v1',
      items: [{ sequence: 0, stage_id: 'tokenizer', metric: 'train_loss', value: 1, unit: 'ratio' }],
      limit: 500,
    })),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ResearchTrainingWorkbenchComponent],
      providers: [{ provide: ModelTrainingApiService, useValue: api }],
    }).compileComponents();
    fixture = TestBed.createComponent(ResearchTrainingWorkbenchComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('capability', {
      available: true, mode: 'local', automatic_release_enabled: true,
    });
  });

  it('renders the live stage timeline, lineage and normalized metrics accessibly', () => {
    const component = fixture.componentInstance;
    component.acceptedRunId = 'run-1';
    component.refresh();
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Run: running');
    expect(text).toContain('Immutable Lineage');
    expect(text).toContain('train_loss = 1 ratio');
    expect(fixture.nativeElement.querySelector('[aria-label="Research-Stage-Timeline"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[aria-label="Research-Artefakt-Lineage"]')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('[aria-label="Research-Evaluationsmetriken"]')).not.toBeNull();
  });
});
