import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { ScrumImprovementApiService } from './scrum-improvement-api.service';

describe('ScrumImprovementApiService', () => {
  const core = { get: vi.fn(() => of({ sprints: [] })) };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [ScrumImprovementApiService, { provide: HubApiCoreService, useValue: core }],
    });
  });

  it('loads the project-scoped Hub read model without worker endpoints', () => {
    TestBed.inject(ScrumImprovementApiService).overview('http://hub.test', 'project / one').subscribe();

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/scrum/overview?scope_id=project%20%2F%20one',
      'http://hub.test', undefined, false,
    );
    expect(JSON.stringify(core.get.mock.calls[0])).not.toContain('worker');
  });
});
