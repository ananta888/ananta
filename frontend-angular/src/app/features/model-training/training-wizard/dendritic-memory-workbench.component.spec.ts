import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ModelTrainingApiService } from '../model-training-api.service';
import { DendriticMemoryCapability } from '../model-training.models';
import { DendriticMemoryWorkbenchComponent } from './dendritic-memory-workbench.component';

describe('DendriticMemoryWorkbenchComponent', () => {
  const api = {
    dryRunDendriticExperiment: vi.fn(() => of({
      admissible: true,
      reason_codes: [],
      spec_digest: 'a'.repeat(64),
      model_download_performed: false,
      worker_call_performed: false,
      human_intervention_required: false,
    })),
    createDendriticExperiment: vi.fn(() => of({
      run_id: 'dendritic-run-1',
      state: 'queued',
      revision: 1,
      replayed: false,
      experimental: true,
      not_production_ready: true,
      claims_not_verified: true,
      human_intervention_required: false,
    })),
    listDendriticRuns: vi.fn(() => of({ items: [], limit: 100 })),
    listDendriticPacks: vi.fn(() => of({ items: [], limit: 100 })),
    cancelDendriticRun: vi.fn(),
    revokeDendriticPack: vi.fn(),
  };
  const capability: DendriticMemoryCapability = {
    schema: 'ananta.dendritic-memory-capability.v1',
    state: 'available',
    available: true,
    experimental: true,
    not_production_ready: true,
    claims_not_verified: true,
    limits: { max_branches: 64, max_hidden_dimension: 4096, max_steps: 100_000 },
    human_intervention_required: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [DendriticMemoryWorkbenchComponent],
      providers: [{ provide: ModelTrainingApiService, useValue: api }],
    });
  });

  it('stays capability-bound and visibly labels all experiment caveats', () => {
    const fixture = TestBed.createComponent(DendriticMemoryWorkbenchComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('capability', { ...capability, available: false, state: 'disabled' });
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('experimental');
    expect(fixture.nativeElement.textContent).toContain('not_production_ready');
    expect(fixture.nativeElement.textContent).toContain('claims_not_verified');
    expect(fixture.nativeElement.textContent).toContain('Experiment nicht verfügbar');
  });

  it('dry-runs and starts through Hub APIs without any human confirmation field', () => {
    const fixture = TestBed.createComponent(DendriticMemoryWorkbenchComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.componentRef.setInput('capability', capability);
    const component = fixture.componentInstance;
    component.datasetDigest = 'a'.repeat(64);
    component.baseModelSnapshotDigest = 'b'.repeat(64);
    component.dryRun();
    component.start();

    expect(api.dryRunDendriticExperiment).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ spec: expect.objectContaining({ job_type: 'train_dendritic_memory' }) }),
    );
    expect(api.createDendriticExperiment).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ spec: expect.objectContaining({ mode: 'live' }) }),
      expect.stringMatching(/^dendritic-experiment-/),
    );
    const body = api.createDendriticExperiment.mock.calls[0][1];
    expect(JSON.stringify(body)).not.toMatch(/confirm|approval|human/i);
    expect(component.acceptedRunId).toBe('dendritic-run-1');
  });
});
