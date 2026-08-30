import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentSafetyApiService } from './agent-safety-api.service';

describe('AgentSafetyApiService', () => {
  const core = { get: vi.fn(() => of({ runs: [] })), post: vi.fn(() => of({ policy_id: 'policy-1' })) };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [AgentSafetyApiService, { provide: HubApiCoreService, useValue: core }],
    });
  });

  it('loads only the Hub-owned project and optional run read model', () => {
    TestBed.inject(AgentSafetyApiService)
      .overview('http://hub.test', 'project / one', 'run-1')
      .subscribe();

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/agent-safety/overview?project_id=project+%2F+one&run_id=run-1',
      'http://hub.test',
      undefined,
      false,
    );
    expect(JSON.stringify(core.get.mock.calls[0])).not.toContain('worker');
  });

  it('submits an explicit automatic Hub preauthorization command', () => {
    TestBed.inject(AgentSafetyApiService).configurePolicy('http://hub.test', {
      policy_id: 'policy-1', revision: 2, mode: 'enforce', preventive_policy_enabled: true,
      preventive_training_enabled: false, sentinel_enabled: true, telemetry_enabled: true,
      external_kill_switch_enabled: true, incident_freeze_enabled: true,
      adversarial_evaluation_enabled: false, adversarial_scope: [], global_stop_scope: 'run',
      max_parallel_agents: 1, automatic_authorization: true,
    }).subscribe();

    expect(core.post).toHaveBeenCalledWith(
      'http://hub.test/api/agent-safety/policies', expect.objectContaining({ automatic_authorization: true }),
      'http://hub.test', undefined, false,
    );
  });
});
