import { of } from 'rxjs';

import { AgentsListComponent } from './agents-list.component';

describe('AgentsListComponent', () => {
  it('probes Docker-internal workers through the configured Hub', () => {
    const system = {
      probeAgent: vi.fn(() => of({
        health: { reachable: true, status: 'degraded' },
        readiness: { ready: false, checks: { hub: 'ok', llm: 'error' } },
      })),
      health: vi.fn(),
      ready: vi.fn(),
    };
    const notifications = { success: vi.fn(), error: vi.fn() };
    const component = Object.create(AgentsListComponent.prototype) as any;
    component.system = system;
    component.ns = notifications;
    component.hub = { name: 'hub', role: 'hub', url: 'https://hub.example' };
    const worker: any = {
      name: 'alpha',
      role: 'worker',
      url: 'http://ai-agent-alpha:5000',
    };

    component.ping(worker);

    expect(system.probeAgent).toHaveBeenCalledWith(
      'https://hub.example',
      'alpha',
    );
    expect(system.health).not.toHaveBeenCalled();
    expect(worker._health).toBe('degraded');
    expect(worker._db).toBe('Not Ready');
    expect(notifications.success).toHaveBeenCalledWith(
      'alpha ist erreichbar (Health degraded)',
    );
  });
});
