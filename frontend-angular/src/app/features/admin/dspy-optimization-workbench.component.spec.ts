import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { DspyOptimizationApiService } from '../../services/dspy-optimization-api.service';
import { DspyOptimizationWorkbenchComponent } from './dspy-optimization-workbench.component';

describe('DspyOptimizationWorkbenchComponent', () => {
  const api = {
    capabilities: vi.fn(() => of({
      state: 'available', reasonCode: 'ready', mode: 'mock', installedVersion: 'mock',
      optimizerCapabilities: ['labeled_few_shot'], programKinds: ['planning_structured_tasks'],
      limits: { max_model_calls: 10 }, humanInterventionRequired: false,
    })),
    runs: vi.fn(() => of([run()])),
    cancel: vi.fn(() => of({ ...run(), state: 'cancelled', revision: 2, reasonCode: 'dspy_job_cancelled_by_policy' })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({ providers: [
      { provide: DspyOptimizationApiService, useValue: api },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.example' }] } },
    ] });
  });

  afterEach(() => TestBed.resetTestingModule());

  it('shows Hub capability and tenant-scoped runs without provider controls', () => {
    const fixture = TestBed.createComponent(DspyOptimizationWorkbenchComponent);
    fixture.componentInstance.tenantId = 'tenant-1';
    fixture.componentInstance.load(); fixture.detectChanges();
    expect(fixture.componentInstance.capability?.state).toBe('available');
    expect(fixture.componentInstance.runs).toHaveLength(1);
    expect(fixture.nativeElement.textContent).toContain('weder Provider noch Orchestrator');
  });

  it('cancels automatically and never opens a human approval wait', () => {
    const fixture = TestBed.createComponent(DspyOptimizationWorkbenchComponent);
    fixture.componentInstance.runs = [run()];
    fixture.componentInstance.cancel(run());
    expect(fixture.componentInstance.runs[0].state).toBe('cancelled');
    expect(fixture.componentInstance.status).toBe('dspy_job_cancelled_by_policy');
  });
});

function run() {
  return {
    tenantId: 'tenant-1', runId: 'run-1', state: 'admitted', revision: 1,
    reasonCode: 'dspy_job_admitted', specDigest: 'a'.repeat(64), artifact: null,
    humanInterventionRequired: false as const,
  };
}
