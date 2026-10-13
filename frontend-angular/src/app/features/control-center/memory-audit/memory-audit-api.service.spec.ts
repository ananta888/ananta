import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { MemoryAuditApiService } from './memory-audit-api.service';

describe('MemoryAuditApiService', () => {
  it('retrieves diffs only from the same authenticated Hub project and Run', () => {
    const core = { get: vi.fn(() => of({})) };
    TestBed.configureTestingModule({ providers: [
      MemoryAuditApiService, { provide: HubApiCoreService, useValue: core },
    ] });
    TestBed.inject(MemoryAuditApiService).diffs('http://hub.test', 'project-a',
      { run_id: 'run-a', channel_id: 'hub-result-memory' }).subscribe();
    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/projects/project-a/memory-audit/diffs?run_id=run-a&channel_id=hub-result-memory',
      'http://hub.test', undefined, false,
    );
  });
  it('uses the authenticated Hub client with encoded project, filters and cursor', () => {
    const core = { get: vi.fn(() => of({})) };
    TestBed.configureTestingModule({ providers: [
      MemoryAuditApiService, { provide: HubApiCoreService, useValue: core },
    ] });
    TestBed.inject(MemoryAuditApiService).events('http://hub.test', 'project space',
      { run_id: 'run / one', channel_id: 'run-scratchpad' }, 42).subscribe();
    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/projects/project%20space/memory-audit/events?page_size=50&after_sequence=42&run_id=run+%2F+one&channel_id=run-scratchpad',
      'http://hub.test', undefined, false,
    );
  });
});
