import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentSafetyApiService } from './agent-safety-api.service';

describe('AgentSafetyApiService', () => {
  const core = { get: vi.fn(() => of({ runs: [] })) };

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
});
