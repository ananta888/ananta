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
      providerProfiles: ['local.default'], metricSets: ['deterministic-v1'], policyDigest: 'a'.repeat(64),
      limits: { max_model_calls: 10 }, humanInterventionRequired: false,
    })),
    runs: vi.fn(() => of([run()])),
    cancel: vi.fn(() => of({ ...run(), state: 'cancelled', revision: 2, reasonCode: 'dspy_job_cancelled_by_policy' })),
    dryRun: vi.fn(() => of({ admissible: true, model_call_performed: false })),
    create: vi.fn(() => of(run())),
    promotePlan: vi.fn(() => of({ reason_code: 'dspy_promoted_by_policy', revision: 1 })),
    rollback: vi.fn(() => of({ reason_code: 'dspy_rollback_applied', revision: 2 })),
    provenance: vi.fn(() => of({ schema: 'ananta.dspy-promotion-provenance.v1', current_revision: 2 })),
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

  it('returns a bounded status when Hub admission fails synchronously', () => {
    api.capabilities.mockImplementationOnce(() => { throw new Error('dspy_hub_endpoint_denied'); });
    const fixture = TestBed.createComponent(DspyOptimizationWorkbenchComponent);
    fixture.componentInstance.tenantId = 'tenant-1';
    fixture.componentInstance.load();
    expect(fixture.componentInstance.loading).toBe(false);
    expect(fixture.componentInstance.status).toBe('dspy_hub_endpoint_denied');
  });

  it('cancels automatically and never opens a human approval wait', () => {
    const fixture = TestBed.createComponent(DspyOptimizationWorkbenchComponent);
    fixture.componentInstance.runs = [run()];
    fixture.componentInstance.cancel(run());
    expect(fixture.componentInstance.runs[0].state).toBe('cancelled');
    expect(fixture.componentInstance.status).toBe('dspy_job_cancelled_by_policy');
  });

  it('runs dry-run, promotion, and rollback through automatic Hub gates', () => {
    const fixture = TestBed.createComponent(DspyOptimizationWorkbenchComponent);
    fixture.componentInstance.tenantId = 'tenant-1';
    fixture.componentInstance.specJson = '{"schema":"spec"}';
    fixture.componentInstance.dryRun();
    expect(fixture.componentInstance.dryRunResult?.['admissible']).toBe(true);
    fixture.componentInstance.promotionPlanJson = '{"schema":"plan"}';
    fixture.componentInstance.evaluationJson = '{"attestation":"value"}';
    fixture.componentInstance.promote();
    expect(fixture.componentInstance.status).toBe('dspy_promoted_by_policy');
    fixture.componentInstance.scopeId = 'planning-en';
    fixture.componentInstance.registryRevision = 1;
    fixture.componentInstance.rollback();
    expect(fixture.componentInstance.status).toBe('dspy_rollback_applied');
    fixture.componentInstance.loadProvenance();
    expect(fixture.componentInstance.provenanceResult?.['current_revision']).toBe(2);
  });
});

function run() {
  return {
    tenantId: 'tenant-1', runId: 'run-1', state: 'admitted', revision: 1,
    reasonCode: 'dspy_job_admitted', specDigest: 'a'.repeat(64), artifact: null,
    usage: null, humanInterventionRequired: false as const,
  };
}
