import { resolveAgentForUrl, resolveAuthTarget } from './auth-target.resolver';

const agents = [
  { name: 'hub', role: 'hub', url: 'http://hub:5000', token: 'hub-secret' },
  { name: 'worker', role: 'worker', url: 'http://worker:5001/base', token: 'worker-secret' },
  { name: 'worker-readonly', role: 'worker', url: 'http://worker-readonly:5002' },
] as any[];

describe('auth target resolver', () => {
  it('matches agent URLs by normalized path boundary', () => {
    expect(resolveAgentForUrl(agents, 'http://worker:5001/base/tasks')?.name).toBe('worker');
    expect(resolveAgentForUrl(agents, 'http://worker:5001/baseball/tasks')).toBeNull();
  });

  it('prefers the longest matching agent base URL', () => {
    const nestedAgents = [
      { name: 'worker-root', role: 'worker', url: 'http://worker:5001', token: 'root-secret' },
      { name: 'worker-api', role: 'worker', url: 'http://worker:5001/api', token: 'api-secret' },
    ] as any[];

    expect(resolveAgentForUrl(nestedAgents, 'http://worker:5001/api/tasks')?.name).toBe('worker-api');
  });

  it('forbids the silent user-token fallback on workers without shared secret', () => {
    // Default-deny: kein User-Token an Worker ohne Shared Secret.
    // Wir wollen NICHT dass der Interceptor den Hub-User-Token an einen
    // Worker-Endpoint schickt; der Worker hat eine andere Auth-Sphäre.
    const target = resolveAuthTarget({
      agents,
      userToken: 'user-token',
      requestUrl: 'http://worker-readonly:5002/tasks',
    });
    expect(target.kind).toBe('passthrough_no_credentials');
    expect(target.refreshOnUnauthorized).toBe(false);
    expect(target.userToken).toBeNull();
  });

  it('keeps shared-secret agent JWT paths separate from user refresh', () => {
    expect(resolveAuthTarget({ agents, userToken: 'user-token', requestUrl: 'http://worker:5001/base/tasks' }))
      .toEqual(expect.objectContaining({
        kind: 'agent_jwt_shared_secret',
        agentSharedSecret: 'worker-secret',
        refreshOnUnauthorized: false,
      }));
  });

  it('passes unknown targets and known targets without credentials through explicitly', () => {
    expect(resolveAuthTarget({ agents, userToken: null, requestUrl: 'http://external:7000/tasks' }))
      .toEqual(expect.objectContaining({
        kind: 'passthrough_unknown_target',
        agent: null,
        refreshOnUnauthorized: false,
      }));
    expect(resolveAuthTarget({ agents, userToken: null, requestUrl: 'http://worker-readonly:5002/tasks' }))
      .toEqual(expect.objectContaining({
        kind: 'passthrough_no_credentials',
        agentSharedSecret: null,
        userToken: null,
        refreshOnUnauthorized: false,
      }));
  });
});
