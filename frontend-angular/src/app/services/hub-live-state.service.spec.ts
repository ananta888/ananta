import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiService } from './hub-api.service';
import { HubLiveStateService } from './hub-live-state.service';

describe('HubLiveStateService', () => {
  let service: HubLiveStateService;
  let hubApi: {
    streamSystemEvents: ReturnType<typeof vi.fn>;
    streamTaskLogs: ReturnType<typeof vi.fn>;
  };
  let directory: {
    list: ReturnType<typeof vi.fn>;
    upsert: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    hubApi = {
      streamSystemEvents: vi.fn(),
      streamTaskLogs: vi.fn(),
    };
    directory = {
      list: vi.fn(() => [{ name: 'hub', role: 'hub', url: 'http://hub:5000', token: 'old-token' }]),
      upsert: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        HubLiveStateService,
        { provide: HubApiService, useValue: hubApi },
        { provide: AgentDirectoryService, useValue: directory },
      ],
    });
    service = TestBed.inject(HubLiveStateService);
  });

  it('updates the hub token from system events', () => {
    hubApi.streamSystemEvents.mockReturnValue(of({ type: 'token_rotated', data: { new_token: 'new-token' } }));

    service.ensureSystemEvents('http://hub:5000');

    expect(directory.upsert).toHaveBeenCalledWith(expect.objectContaining({ url: 'http://hub:5000', token: 'new-token' }));
    expect(service.systemStreamConnected()).toBe(true);
    expect(service.lastSystemEvent()).toEqual(expect.objectContaining({ type: 'token_rotated' }));
    expect(service.snapshot()).toEqual(expect.objectContaining({ systemStreamConnected: true, activeTaskLogStreams: 0 }));
  });

  it('deduplicates task logs and flags refresh-worthy events', () => {
    hubApi.streamTaskLogs.mockReturnValue(of(
      { timestamp: 1, command: 'echo hi', event_type: 'execution_result', reason: 'done' },
      { timestamp: 1, command: 'echo hi', event_type: 'execution_result', reason: 'done' },
      { timestamp: 2, event_type: 'proposal_review', reason: 'approved' },
    ));

    service.watchTaskLogs('http://hub:5000', 'T-1', { reset: true });

    const state = service.taskLogState('T-1');
    expect(state.logs).toHaveLength(2);
    expect(state.connected).toBe(true);
    expect(service.snapshot().activeTaskLogStreams).toBe(1);
    expect(service.shouldRefreshTask({ event_type: 'execution_result' })).toBe(true);
    expect(service.shouldRefreshTask({ event_type: 'log_line' })).toBe(false);
  });

  it('marks task streams disconnected after errors', () => {
    hubApi.streamTaskLogs.mockReturnValue(throwError(() => new Error('offline')));

    service.watchTaskLogs('http://hub:5000', 'T-2', { reset: true });

    expect(service.taskLogState('T-2').connected).toBe(false);
    expect(service.taskLogState('T-2').loading).toBe(false);
  });
});
