import { of, throwError } from 'rxjs';

import { DashboardComponent } from './dashboard.component';

describe('DashboardComponent (benchmarks)', () => {
  const hubApiMock = {
    getDashboardReadModel: vi.fn(),
    getLlmBenchmarks: vi.fn(),
    getStatsHistory: vi.fn(() => of([])),
    listTeams: vi.fn(() => of([])),
    listTeamRoles: vi.fn(() => of([])),
    listAgents: vi.fn(() => of([])),
    getAutopilotStatus: vi.fn(() => of({})),
  };

  function createComponent(): DashboardComponent {
    const cmp = Object.create(DashboardComponent.prototype) as DashboardComponent & { hubApi: any };
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.benchmarkTaskKind = 'analysis';
    cmp.benchmarkData = [];
    cmp.benchmarkUpdatedAt = null;
    cmp.hubApi = hubApiMock;
    return cmp;
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads benchmark recommendations and updated timestamp', () => {
    hubApiMock.getLlmBenchmarks.mockReturnValue(
      of({
        updated_at: 1739790000,
        items: [{ id: 'aider:gpt-4o-mini', provider: 'aider', model: 'gpt-4o-mini', focus: { suitability_score: 88.5 } }],
      })
    );

    const cmp = createComponent();
    cmp.benchmarkTaskKind = 'coding';
    cmp.refreshBenchmarks();

    expect(hubApiMock.getLlmBenchmarks).toHaveBeenCalledWith('http://hub:5000', { task_kind: 'coding', top_n: 8 });
    expect(cmp.benchmarkData.length).toBe(1);
    expect(cmp.benchmarkData[0].provider).toBe('aider');
    expect(cmp.benchmarkUpdatedAt).toBe(1739790000);
  });

  it('passes benchmark task kind into dashboard read model refresh', () => {
    hubApiMock.getDashboardReadModel.mockReturnValue(
      of({
        system_health: {
          status: 'ok',
          agent: 'hub',
          checks: {
            queue: { depth: 2 },
            registration: { enabled: true, status: 'ok', attempts: 1 },
          },
        },
        agents: { count: 0, items: [] },
        teams: { items: [] },
        roles: { items: [] },
        tasks: { counts: {}, recent: [] },
        benchmarks: {
          task_kind: 'coding',
          updated_at: 1739790000,
          items: [{ id: 'codex:gpt-5-codex', provider: 'codex', model: 'gpt-5-codex', focus: { suitability_score: 91.2 } }],
        },
        context_timestamp: 1739790000,
      })
    );

    const cmp = createComponent();
    (cmp as any).dir = { list: () => [cmp.hub] };
    (cmp as any).ns = { error: vi.fn() };
    cmp.benchmarkTaskKind = 'coding';
    cmp.refresh();

    expect(hubApiMock.getDashboardReadModel).toHaveBeenCalledWith('http://hub:5000', { benchmarkTaskKind: 'coding' });
    expect(cmp.benchmarkTaskKind).toBe('coding');
    expect(cmp.benchmarkData[0].id).toBe('codex:gpt-5-codex');
    expect(cmp.benchmarkUpdatedAt).toBe(1739790000);
  });

  it('falls back to empty benchmark list on API error', () => {
    hubApiMock.getLlmBenchmarks.mockReturnValue(throwError(() => new Error('offline')));

    const cmp = createComponent();
    cmp.benchmarkData = [{ id: 'old' }];
    cmp.benchmarkUpdatedAt = 1;
    cmp.refreshBenchmarks();

    expect(cmp.benchmarkData).toEqual([]);
    expect(cmp.benchmarkUpdatedAt).toBe(1);
  });
});
