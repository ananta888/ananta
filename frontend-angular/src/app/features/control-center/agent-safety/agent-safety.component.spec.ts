import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { AgentSafetyApiService } from './agent-safety-api.service';
import { AgentSafetyComponent } from './agent-safety.component';

describe('AgentSafetyComponent', () => {
  let fixture: ComponentFixture<AgentSafetyComponent>;
  const state = {
    selectedProjectId$: new BehaviorSubject('project-1'),
    hubBaseUrl: vi.fn(() => 'http://hub.test'),
  };
  const api = {
    configurePolicy: vi.fn(() => of({})),
    overview: vi.fn(() => of({
      policies: [],
      runs: [{
        run_id: 'run-1', project_id: 'project-1', mode: 'adversarial_eval', state: 'freeze',
        execution_allowed: false, policy_id: 'policy-1', policy_revision: 2,
        agents: [{ agent_id: 'agent-1', sandbox_id: 'sandbox-1', state: 'active' }],
      }],
      incidents: [{
        bundle_id: 'bundle-1', run_id: 'run-1', event_count: 1,
        bundle_digest: 'a'.repeat(64), created_at: '2026-08-29T00:00:00Z',
      }],
      controls: [],
      events: [{
        event_id: 'event-1', event_type: 'boundary_crossing', severity: 'critical',
        source: 'detector-1', observed_at: '2026-08-29T00:00:00Z', event_digest: 'b'.repeat(64),
      }],
      metrics: {
        boundary_outcomes: { crossed: 1 }, boundary_classes: { network: 1 }, self_reports: 0,
        external_observations: 1, containment_receipt_failures: 0, incident_count: 1,
        open_critical_findings: 1, incident_replay_coverage: 0,
      },
      containment_available: true,
      human_intervention_required: false,
    })),
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    await TestBed.configureTestingModule({
      imports: [AgentSafetyComponent],
      providers: [
        { provide: ControlCenterStateFacade, useValue: state },
        { provide: AgentSafetyApiService, useValue: api },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(AgentSafetyComponent);
    fixture.detectChanges();
  });

  it('shows stopped execution, incidents and critical boundary evidence', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Containment-Adapter');
    expect(text).toContain('Ausführung gesperrt');
    expect(text).toContain('bundle-1');
    expect(text).toContain('boundary_crossing');
    expect(text).toContain('Offene Findings 1');
    expect(text).toContain('Policy automatisch prüfen und speichern');
    expect(api.overview).toHaveBeenCalledWith('http://hub.test', 'project-1', undefined);
  });
});
