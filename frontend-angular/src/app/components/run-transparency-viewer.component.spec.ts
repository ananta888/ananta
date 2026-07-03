import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RunTransparencyViewerComponent } from './run-transparency-viewer.component';
import { RunTransparencyReport, RunStepTrace } from '../models/transparency.models';

function makeReport(overrides: Partial<RunTransparencyReport> = {}): RunTransparencyReport {
  return {
    run_id: 'run-123', goal_id: 'g-1',
    steps: [], overall_status: 'completed',
    local_only_mode: false, has_external_providers: false,
    total_policy_blockades: 0, verification_hash: null,
    ...overrides,
  };
}

function makeStep(overrides: Partial<RunStepTrace> = {}): RunStepTrace {
  return {
    step_id: 's1', step_name: 'Test Step', expert_id: null, state: 'completed',
    tool_calls: [], diff_proposals: [], approval_gates: [], evidence: [],
    model_claims: [], verified_facts: [], local_only: false, policy_blockades: [],
    started_at: Date.now(), duration_ms: 100, ...overrides,
  };
}

describe('RunTransparencyViewerComponent', () => {
  let fixture: ComponentFixture<RunTransparencyViewerComponent>;
  let component: RunTransparencyViewerComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RunTransparencyViewerComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(RunTransparencyViewerComponent);
    component = fixture.componentInstance;
  });

  it('should show placeholder when no report', () => {
    component.report = null;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Kein Transparency-Report');
  });

  it('should show run_id prefix', () => {
    component.report = makeReport();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('run-123'.slice(0, 8));
  });

  it('should show local-only badge when local_only_mode=true', () => {
    component.report = makeReport({ local_only_mode: true });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Local Only');
  });

  it('should show external provider badge', () => {
    component.report = makeReport({ has_external_providers: true });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Externe Provider');
  });

  it('should show blockade count', () => {
    component.report = makeReport({ total_policy_blockades: 3 });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('3 Blockaden');
  });

  it('should show verification badge when hash present', () => {
    component.report = makeReport({ verification_hash: 'abc123' });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Verifiziert');
  });

  it('should render step name', () => {
    component.report = makeReport({ steps: [makeStep({ step_name: 'Planning' })] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Planning');
  });

  it('should show policy blockade', () => {
    const step = makeStep({
      policy_blockades: [{ action_attempted: 'write_file', blocked_reason: 'policy denied', rule: 'no_write', severity: 'hard_block' }]
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('write_file');
    expect(fixture.nativeElement.textContent).toContain('policy denied');
  });

  it('should show context trace info', () => {
    const step = makeStep({
      context_trace: {
        trace_id: 't1', query: 'find auth', provider: 'codecompass',
        selected_count: 3, discarded_count: 1,
        budget_chars_used: 500, budget_chars_limit: 40000,
        has_external_evidence: false, policy_decisions: [],
      }
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('codecompass');
    expect(fixture.nativeElement.textContent).toContain('3 Treffer');
  });

  it('should mark external evidence in context trace', () => {
    const step = makeStep({
      context_trace: {
        trace_id: 't1', query: 'q', provider: 'augment_mcp',
        selected_count: 1, discarded_count: 0,
        budget_chars_used: 100, budget_chars_limit: 40000,
        has_external_evidence: true, policy_decisions: [],
      }
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Externer Provider');
  });

  it('should show delegation trace info', () => {
    const step = makeStep({
      delegation_trace: {
        trace_id: 'd1', goal_summary: 'Fix bug',
        chosen_worker_id: 'pr_author', chosen_expert_id: null,
        selection_reason: 'best fit', tools_granted: ['read_file'],
        alternatives_considered: [],
      }
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('pr_author');
    expect(fixture.nativeElement.textContent).toContain('read_file');
  });

  it('should show model claims separately from verified facts', () => {
    const step = makeStep({
      model_claims: ['I fixed the bug'],
      verified_facts: ['Tests pass'],
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('I fixed the bug');
    expect(text).toContain('Tests pass');
  });

  it('should show diff proposal status', () => {
    const step = makeStep({
      diff_proposals: [{
        proposal_id: 'dp1', total_files: 2,
        total_lines_added: 10, total_lines_removed: 3,
        risk_summary: 'low', status: 'approved',
        policy_check_passed: true, is_applicable: true,
      }]
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('+10');
    expect(fixture.nativeElement.textContent).toContain('approved');
  });

  it('should show approval gate status', () => {
    const step = makeStep({
      approval_gates: [{
        gate_id: 'g1', gate_type: 'apply_diff',
        risk_level: 'high', status: 'pending',
        expires_at: null, decided_by: null,
      }]
    });
    component.report = makeReport({ steps: [step] });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('apply_diff');
    expect(fixture.nativeElement.textContent).toContain('pending');
  });

  it('stateClass should return correct class', () => {
    expect(component.stateClass('completed')).toBe('badge-approved');
    expect(component.stateClass('failed')).toBe('badge-blocked');
    expect(component.stateClass('running')).toBe('badge-pending');
  });
});
