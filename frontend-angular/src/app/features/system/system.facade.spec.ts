import { of } from 'rxjs';

import { SystemFacade } from './system.facade.ts';

describe('SystemFacade', () => {
  it('delegates agent status polling and system events to shared seams', () => {
    const facade = Object.create(SystemFacade.prototype) as SystemFacade & {
      agentStatusState: any;
      liveState: any;
      agentDirectory: any;
    };
    facade.agentDirectory = {
      list: () => [{ name: 'hub', url: 'http://hub:5000', role: 'hub' }],
    };
    facade.agentStatusState = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      reload: vi.fn(),
      loading: vi.fn(() => false),
      lastLoadedAt: vi.fn(() => 123),
      error: vi.fn(() => null),
      statusFor: vi.fn(() => 'online'),
    };
    facade.liveState = {
      ensureSystemEvents: vi.fn(),
      disconnectSystemEvents: vi.fn(),
      systemStreamConnected: vi.fn(() => true),
      lastSystemEvent: vi.fn(() => ({ event_type: 'system' })),
    };

    facade.connectAgentStatuses(undefined, 9000);
    facade.reloadAgentStatuses();
    facade.ensureSystemEvents();

    expect(facade.agentStatusState.connect).toHaveBeenCalledWith('http://hub:5000', 9000);
    expect(facade.agentStatusState.reload).toHaveBeenCalled();
    expect(facade.liveState.ensureSystemEvents).toHaveBeenCalledWith('http://hub:5000');
    expect(facade.agentStatus('hub')).toBe('online');
    expect(facade.systemStreamConnected()).toBe(true);
  });

  it('delegates hub and agent api calls through existing services', () => {
    const facade = Object.create(SystemFacade.prototype) as SystemFacade & {
      agentApi: any;
      hubApi: any;
    };
    facade.agentApi = {
      getConfig: vi.fn(() => of({ default_provider: 'lmstudio' })),
      setConfig: vi.fn(() => of({ ok: true })),
      getLlmHistory: vi.fn(() => of([])),
      health: vi.fn(() => of({ status: 'success' })),
      ready: vi.fn(() => of({ ready: true })),
    };
    facade.hubApi = {
      getAuditLogs: vi.fn(() => of([])),
      analyzeAuditLogs: vi.fn(() => of({ analysis: 'ok' })),
      getTriggersStatus: vi.fn(() => of({ enabled_sources: [] })),
      configureTriggers: vi.fn(() => of({ ok: true })),
      testTrigger: vi.fn(() => of({ would_create: 1 })),
      listProviderCatalog: vi.fn(() => of({ providers: [] })),
      getLlmBenchmarksConfig: vi.fn(() => of({ retention: {} })),
    };

    facade.getConfig('http://hub:5000').subscribe();
    facade.setConfig('http://hub:5000', { a: 1 }).subscribe();
    facade.getAuditLogs('http://hub:5000').subscribe();
    facade.getTriggersStatus('http://hub:5000').subscribe();

    expect(facade.agentApi.getConfig).toHaveBeenCalledWith('http://hub:5000', undefined);
    expect(facade.agentApi.setConfig).toHaveBeenCalledWith('http://hub:5000', { a: 1 }, undefined);
    expect(facade.hubApi.getAuditLogs).toHaveBeenCalledWith('http://hub:5000', 100, 0, undefined);
    expect(facade.hubApi.getTriggersStatus).toHaveBeenCalledWith('http://hub:5000', undefined);
  });
});
