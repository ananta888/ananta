import { of, throwError } from 'rxjs';

import { SystemAgentStatusStateService } from './system-agent-status-state.service.ts';

describe('SystemAgentStatusStateService', () => {
  it('normalizes listAgents array responses into status records', () => {
    const service = Object.create(SystemAgentStatusStateService.prototype) as SystemAgentStatusStateService & {
      hubApi: any;
      statuses: any;
      loading: any;
      lastLoadedAt: any;
      error: any;
      inFlight: boolean;
      hubUrl: string;
    };
    service.hubApi = {
      listAgents: vi.fn(() => of([{ name: 'hub', status: 'online' }, { name: 'worker-a', status: 'offline' }])),
    };
    service.statuses = { set: vi.fn(), call: vi.fn() };
    service.loading = { set: vi.fn() };
    service.lastLoadedAt = { set: vi.fn() };
    service.error = { set: vi.fn() };
    service.inFlight = false;
    service.hubUrl = 'http://hub:5000';

    service.reload();

    expect(service.hubApi.listAgents).toHaveBeenCalledWith('http://hub:5000');
    expect(service.statuses.set).toHaveBeenCalledWith({ hub: 'online', 'worker-a': 'offline' });
    expect(service.error.set).toHaveBeenCalledWith(null);
  });

  it('stores a readable error when polling fails', () => {
    const service = Object.create(SystemAgentStatusStateService.prototype) as SystemAgentStatusStateService & {
      hubApi: any;
      loading: any;
      error: any;
      inFlight: boolean;
      hubUrl: string;
    };
    service.hubApi = {
      listAgents: vi.fn(() => throwError(() => new Error('down'))),
    };
    service.loading = { set: vi.fn() };
    service.error = { set: vi.fn() };
    service.inFlight = false;
    service.hubUrl = 'http://hub:5000';

    service.reload();

    expect(service.error.set).toHaveBeenCalledWith('Agentenstatus konnte nicht geladen werden');
  });
});
