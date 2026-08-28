import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { KnowledgeExpertApiService } from './knowledge-expert-api.service';

describe('KnowledgeExpertApiService', () => {
  const core = {
    get: vi.fn(() => of({ active_banks: [], candidate_banks: [] })),
    post: vi.fn(() => of({ reason_code: 'command_submitted' })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [KnowledgeExpertApiService, { provide: HubApiCoreService, useValue: core }],
    });
  });

  it('uses only the authenticated Hub control-plane endpoints', () => {
    const service = TestBed.inject(KnowledgeExpertApiService);
    service.snapshot('http://hub.test').subscribe();
    service.command('http://hub.test', {
      schema: 'ananta.knowledge-expert-control-command.v1',
      action: 'rollback',
      bank_id: 'bank-1',
      generation_id: 'generation-1',
      expected_generation_id: 'generation-2',
      reason: 'restore last good generation',
      confirmed: true,
    }).subscribe();

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/knowledge-experts', 'http://hub.test', undefined, false,
    );
    expect(core.post.mock.calls[0][0]).toBe('http://hub.test/api/knowledge-experts/commands');
    expect(JSON.stringify(core.post.mock.calls[0])).not.toContain('worker');
  });
});
