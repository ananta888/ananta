import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';

import { SystemFacade } from '../system/system.facade';
import { WorkflowRuntimeOperationsApiService } from './workflow-runtime-operations-api.service';
import { WorkflowRuntimeOperationsComponent } from './workflow-runtime-operations.component';
import {
  RuntimeCapabilityMatrixProjection,
  RuntimeOperationsResponse,
  WorkflowRuntimeOperationRun,
} from './workflow-runtime-operations.models';

describe('WorkflowRuntimeOperationsComponent', () => {
  const api = {
    capabilities: vi.fn(),
    list: vi.fn(),
    command: vi.fn(),
  };
  const system = {
    resolveHubAgent: vi.fn(() => ({ name: 'hub', role: 'hub', url: 'http://hub.test' })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    api.capabilities.mockReturnValue(of(capabilityProjection()));
    TestBed.configureTestingModule({
      imports: [WorkflowRuntimeOperationsComponent],
      providers: [
        { provide: WorkflowRuntimeOperationsApiService, useValue: api },
        { provide: SystemFacade, useValue: system },
      ],
    });
  });

  it('renders an explicit loading state while the Hub read model is pending', () => {
    api.list.mockReturnValue(new Subject<RuntimeOperationsResponse>());
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="runtime-operations-loading"]')).toBeTruthy();
    expect(api.list).toHaveBeenCalledWith('http://hub.test', expect.any(Object));
  });

  it('renders the tenant-scoped empty state without reaching a Worker or Temporal endpoint', () => {
    api.list.mockReturnValue(of(response([])));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Keine Runtime-Läufe');
    expect(text).toContain('keine Hub-Evaluationen');
    expect(fixture.nativeElement.querySelector('[data-testid="runtime-operations-empty"]')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('[data-testid="runtime-capability-matrix"]')).toBeTruthy();
    expect(text).toContain('ananta-native');
    expect(text).toContain('temporal_cluster_required');
  });

  it('uses the canonical production id for the Native runtime filter', () => {
    api.list.mockReturnValue(of(response([])));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.runtimeOptions()).toContain('ananta-native');
    expect(fixture.componentInstance.runtimeOptions()).not.toContain('native');
  });

  it.each([
    ['ready', 'compatible', 'runtime_capabilities_satisfied', []],
    ['disabled', 'blocked', 'runtime_health_disabled', []],
    ['degraded', 'degraded', 'runtime_health_degraded', []],
    ['ready', 'incompatible', 'runtime_capabilities_missing', ['durability']],
  ] as const)(
    'renders %s health and %s capability selection without claiming success',
    (health, selection, reasonCode, missingCapabilities) => {
      const projection = capabilityProjection();
      projection.runtimes[0].health = {
        status: health,
        reason_code: `runtime_health_${health}`,
      };
      projection.runtimes[0].selection = {
        state: selection,
        reason_code: reasonCode,
        missing_capabilities: [...missingCapabilities],
      };
      api.capabilities.mockReturnValue(of(projection));
      api.list.mockReturnValue(of(response([])));

      const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
      fixture.detectChanges();

      const runtime = fixture.nativeElement.querySelector(
        '[data-runtime-id="ananta-native"]',
      ) as HTMLElement;
      expect(runtime.textContent).toContain(health);
      expect(runtime.textContent).toContain(selection);
      expect(runtime.textContent).toContain(reasonCode);
      if (missingCapabilities.length) {
        expect(runtime.textContent).toContain('Fehlend: durability');
      }
    },
  );

  it('shows degraded, stale, parity, fallback, cost, latency, recovery, gates and evidence', () => {
    const run = operationRun({
      status: 'completed',
      outcome_claim: 'unverified',
      degraded: true,
      stale: true,
      degraded_reasons: ['fallback_observed', 'success_without_verified_evidence'],
      fallbacks: [{
        source_runtime: 'langgraph', target_runtime: 'native', reason_code: 'compiled_failed',
        semantic_class: 'control_flow_changed', approved: true,
      }],
      parity_gaps: [{ code: 'native_interrupt_gap', category: 'parity', severity: 'warning', summary: 'Interrupt differs' }],
      evidence: [{
        evidence_id: 'ev-1', kind: 'test', verification_status: 'unverified',
        summary: 'No verified artifact', source_ref: null, observed_at: 10,
      }],
      gates: [{
        gate_id: 'gate-1', label: 'Release Gate', status: 'open', approval_id: null,
        required_evidence_refs: [], allowed_commands: [], expires_at: null,
      }],
      open_gate_count: 1,
    });
    api.list.mockReturnValue(of(response([run])));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Unbestätigt · Evidence fehlt');
    expect(text).toContain('degraded');
    expect(text).toContain('stale');
    expect(text).toContain('langgraph → native');
    expect(text).toContain('compiled_failed');
    expect(text).toContain('native_interrupt_gap');
    expect(text).toContain('Recovery');
    expect(text).toContain('Release Gate');
    expect(text).toContain('No verified artifact');
    expect(text).toContain('0.0042 CU');
    expect(text).toContain('81.5 ms');
  });

  it('renders forbidden separately from a generic unavailable state', () => {
    api.list.mockReturnValue(throwError(() => ({
      status: 403,
      error: { reason_code: 'runtime_operations_forbidden' },
    })));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="runtime-operations-forbidden"]')).toBeTruthy();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Zugriff verweigert');
  });

  it('keeps commands disabled when verified evidence or approval is missing', () => {
    const run = operationRun({ evidence: [], gates: [] });
    api.list.mockReturnValue(of(response([run])));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.canCommand(run, 'retry_run_or_task')).toBe(false);
    fixture.componentInstance.sendCommand(run, 'retry_run_or_task');
    expect(api.command).not.toHaveBeenCalled();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('verifizierte Evidence fehlt');
  });

  it('sends an evidence- and approval-bound command only to the Hub API service', () => {
    const run = operationRun({
      evidence: [{
        evidence_id: 'ev-verified', kind: 'acceptance', verification_status: 'verified',
        summary: 'Gate passed', source_ref: 'artifact-1', observed_at: Date.now() / 1000,
      }],
      gates: [{
        gate_id: 'gate-command', label: 'Operator', status: 'approved', approval_id: 'approval-1',
        required_evidence_refs: ['ev-verified'], allowed_commands: ['retry_run_or_task'],
        expires_at: Date.now() / 1000 + 300,
      }],
      verified_evidence_count: 1,
    });
    api.list.mockReturnValue(of(response([run])));
    api.command.mockReturnValue(of({
      status: 'ok',
      command: { command_id: 'cmd-1', type: 'retry_run_or_task', status: 'accepted', run_id: run.run_id },
    }));
    const fixture = TestBed.createComponent(WorkflowRuntimeOperationsComponent);
    fixture.detectChanges();

    fixture.componentInstance.sendCommand(run, 'retry_run_or_task');
    fixture.detectChanges();

    expect(api.command).toHaveBeenCalledWith(
      'http://hub.test',
      'run-1',
      { type: 'retry_run_or_task', approval_id: 'approval-1', evidence_refs: ['ev-verified'] },
      expect.any(String),
    );
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Hub-Command cmd-1 · accepted');
  });
});

function capabilityProjection(): RuntimeCapabilityMatrixProjection {
  return {
    schema: 'ananta.workflow_runtime_capability_matrix.v1',
    matrix_version: '1.0.0',
    required_capabilities: [],
    runtimes: [
      {
        schema: 'ananta.workflow_runtime_capability.v1',
        runtime_id: 'ananta-native',
        runtime_version: '1.0.0',
        contract_version: 'ananta.execution_plan.v1',
        mode: 'live',
        capabilities: ['checkpoint', 'resume'],
        restrictions: ['hub_task_queue_required'],
        health: { status: 'ready', reason_code: 'runtime_health_ready' },
        selection: { state: 'compatible', reason_code: 'runtime_capabilities_satisfied', missing_capabilities: [] },
      },
      {
        schema: 'ananta.workflow_runtime_capability.v1',
        runtime_id: 'temporal',
        runtime_version: '1.0.0',
        contract_version: 'ananta.execution_plan.v1',
        mode: 'durable',
        capabilities: ['durability', 'resume'],
        restrictions: ['temporal_cluster_required'],
        health: { status: 'ready', reason_code: 'runtime_health_ready' },
        selection: { state: 'compatible', reason_code: 'runtime_capabilities_satisfied', missing_capabilities: [] },
      },
    ],
  };
}

function response(runs: WorkflowRuntimeOperationRun[]): RuntimeOperationsResponse {
  return {
    schema: 'ananta.workflow_runtime_operations_list.v1',
    generated_at: Date.now() / 1000,
    filters: {},
    summary: {
      total_runs: runs.length,
      degraded_runs: runs.filter((run) => run.degraded).length,
      stale_runs: runs.filter((run) => run.stale).length,
      unverified_successes: runs.filter((run) => run.outcome_claim === 'unverified').length,
      open_gates: runs.reduce((total, run) => total + run.open_gate_count, 0),
      verified_evidence: runs.reduce((total, run) => total + run.verified_evidence_count, 0),
      total_cost_micros: runs.reduce((total, run) => total + run.cost_micros, 0),
      latency_p50_ms: runs[0]?.latency_ms || 0,
      latency_p95_ms: runs[0]?.latency_ms || 0,
      active_recoveries: 0,
      parity_gap_runs: runs.filter((run) => run.parity_gaps.length > 0).length,
    },
    runs,
    count: runs.length,
  };
}

function operationRun(overrides: Partial<WorkflowRuntimeOperationRun> = {}): WorkflowRuntimeOperationRun {
  return {
    schema: 'ananta.workflow_runtime_operations_record.v1',
    run_id: 'run-1',
    workflow_id: 'workflow-1',
    task_id: 'task-1',
    runtime: 'langgraph',
    mode: 'compiled',
    status: 'running',
    outcome_claim: 'running',
    capabilities: [{ name: 'checkpoint', status: 'supported', reason_code: null }],
    fallbacks: [],
    cost_micros: 4200,
    latency_ms: 81.5,
    recovery: { status: 'ready', strategy: 'checkpoint', attempts: 1, last_checkpoint_ref: 'cp-1', reason_code: null },
    gates: [],
    evidence: [],
    parity_gaps: [],
    semantic_deviations: [],
    open_gate_count: 0,
    verified_evidence_count: 0,
    degraded: false,
    degraded_reasons: [],
    stale: false,
    updated_at: Date.now() / 1000,
    stale_after_seconds: 60,
    source_sequence: 10,
    ...overrides,
  };
}
