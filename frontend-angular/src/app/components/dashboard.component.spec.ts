import { of, throwError } from 'rxjs';

import { DashboardComponent } from './dashboard.component.ts';

describe('DashboardComponent (benchmarks)', () => {
  const hubApiMock = {
    getDashboardReadModel: vi.fn(),
    getLlmBenchmarks: vi.fn(),
    getStatsHistory: vi.fn(() => of([])),
    listTeams: vi.fn(() => of([])),
    listTeamRoles: vi.fn(() => of([])),
    listAgents: vi.fn(() => of([])),
    getAutopilotStatus: vi.fn(() => of({})),
    listGoals: vi.fn(() => of([])),
    getGoalDetail: vi.fn(() => of(null)),
    getGoalGovernanceSummary: vi.fn(() => of(null)),
    tasks: vi.fn(() => []),
    tasksLoading: vi.fn(() => false),
    tasksLastLoadedAt: vi.fn(() => 1739790000),
    taskCollectionError: vi.fn(() => null),
    connectTaskCollection: vi.fn(),
    disconnectTaskCollection: vi.fn(),
  };

  function createComponent(): DashboardComponent {
    const cmp = Object.create(DashboardComponent.prototype) as DashboardComponent & { hubApi: any; liveState: any };
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.benchmarkTaskKind = 'analysis';
    cmp.benchmarkData = [];
    cmp.benchmarkUpdatedAt = null;
    cmp.benchmarkRecommendation = null;
    cmp.llmDefaults = null;
    cmp.llmExplicitOverride = null;
    cmp.llmEffectiveRuntime = null;
    cmp.hubCopilotStatus = null;
    cmp.contextPolicyStatus = null;
    cmp.researchBackendStatus = null;
    cmp.goalsList = [];
    cmp.selectedGoalId = '';
    cmp.goalDetail = null;
    cmp.goalGovernance = null;
    cmp.goalReportingLoading = false;
    cmp.hubApi = hubApiMock;
    cmp.liveState = { ensureSystemEvents: vi.fn(), systemStreamConnected: () => false, lastSystemEvent: () => null };
    cmp.taskFacade = hubApiMock;
    cmp.ns = { error: vi.fn() } as any;
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
            scheduler: { running: true, scheduled_count: 3 },
          },
        },
        contracts: {
          version: 'v1',
          schema_count: 12,
          task_statuses: {
            canonical_values: ['todo', 'assigned', 'completed'],
          },
        },
        agents: { count: 0, items: [] },
        teams: { items: [] },
        roles: { items: [] },
        tasks: { counts: {}, recent: [] },
        benchmarks: {
          task_kind: 'coding',
          updated_at: 1739790000,
          recommendation: {
            current: { provider: 'openai', model: 'gpt-4o' },
            recommended: { provider: 'codex', model: 'gpt-5-codex', selection_source: 'benchmarks_available_top_ranked' },
            has_explicit_override: false,
            is_recommendation_active: false,
          },
          items: [{ id: 'codex:gpt-5-codex', provider: 'codex', model: 'gpt-5-codex', focus: { suitability_score: 91.2 } }],
        },
        llm_configuration: {
          defaults: { provider: 'openai', model: 'gpt-4o', source: { provider: 'agent_config.default_provider' } },
          explicit_override: { active: false, provider: null, model: null },
          effective_runtime: {
            provider: 'codex',
            model: 'gpt-5-codex',
            mode: 'benchmark_recommendation',
            selection_source: 'benchmarks_available_top_ranked',
            benchmark_applied: true,
            replaces_configured: true,
          },
          hub_copilot: { enabled: true, active: true, strategy_mode: 'planning_and_routing' },
          context_bundle_policy: { effective: { mode: 'standard', compact_max_chunks: 2, standard_max_chunks: 12 } },
          research_backend: {
            provider: 'deerflow',
            enabled: true,
            configured: true,
            review_policy: { required: true, reason: 'research_backend_review_required' },
            providers: {
              deerflow: { provider: 'deerflow', selected: true, configured: true, binary_available: true, working_dir_exists: true, mode: 'cli' },
              ananta_research: { provider: 'ananta_research', selected: false, configured: false, binary_available: false, working_dir_exists: false, mode: 'cli' },
            },
          },
        },
        context_timestamp: 1739790000,
      })
    );

    const cmp = createComponent();
    (cmp as any).dir = { list: () => [cmp.hub] };
    (cmp as any).ns = { error: vi.fn() };
    hubApiMock.tasks.mockReturnValue([
      { id: 'T-2', status: 'completed', updated_at: 1739791000 },
      { id: 'T-1', status: 'todo', updated_at: 1739790000 },
    ]);
    cmp.benchmarkTaskKind = 'coding';
    cmp.refresh();

    expect(hubApiMock.getDashboardReadModel).toHaveBeenCalledWith('http://hub:5000', { benchmarkTaskKind: 'coding' });
    expect(cmp.benchmarkTaskKind).toBe('coding');
    expect(cmp.benchmarkData[0].id).toBe('codex:gpt-5-codex');
    expect(cmp.benchmarkUpdatedAt).toBe(1739790000);
    expect(cmp.benchmarkRecommendation?.recommended?.selection_source).toBe('benchmarks_available_top_ranked');
    expect(cmp.llmEffectiveRuntime?.benchmark_applied).toBe(true);
    expect(cmp.hubCopilotStatus?.active).toBe(true);
    expect(cmp.contextPolicyStatus?.effective?.mode).toBe('standard');
    expect(cmp.researchBackendStatus?.provider).toBe('deerflow');
    expect(cmp.researchBackendProviderEntries()).toHaveLength(2);
    expect(cmp.stats.tasks.total).toBe(2);
    expect(cmp.taskTimeline[0].task_id).toBe('T-2');
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

  it('loads goal governance and cost reporting for the latest goal', () => {
    hubApiMock.listGoals.mockReturnValue(
      of([
        { id: 'goal-older', summary: 'Older goal', updated_at: 10 },
        { id: 'goal-newer', summary: 'Newest goal', updated_at: 20 },
      ])
    );
    hubApiMock.getGoalDetail.mockImplementation((_: string, goalId: string) =>
      of({
        goal: { id: goalId, summary: `Goal ${goalId}`, status: 'planned' },
        tasks: [
          { id: 'task-1', title: 'Expensive task', status: 'completed', verification_status: { status: 'passed' }, cost_summary: { cost_units: 2.5, tokens_total: 1200 } },
          { id: 'task-2', title: 'Cheap task', status: 'completed', verification_status: { status: 'passed' }, cost_summary: { cost_units: 0.5, tokens_total: 300 } },
        ],
      })
    );
    hubApiMock.getGoalGovernanceSummary.mockImplementation((_: string, goalId: string) =>
      of({
        goal_id: goalId,
        verification: { total: 2, passed: 2, failed: 0, escalated: 0 },
        policy: { approved: 1, blocked: 0 },
        cost_summary: { total_cost_units: 3.0, tasks_with_cost: 2, total_tokens: 1500, total_latency_ms: 900 },
        summary: { task_count: 2 },
      })
    );

    const cmp = createComponent();
    cmp.refreshGoalReporting();

    expect(hubApiMock.listGoals).toHaveBeenCalledWith('http://hub:5000');
    expect(hubApiMock.getGoalDetail).toHaveBeenCalledWith('http://hub:5000', 'goal-newer');
    expect(hubApiMock.getGoalGovernanceSummary).toHaveBeenCalledWith('http://hub:5000', 'goal-newer');
    expect(cmp.selectedGoalId).toBe('goal-newer');
    expect(cmp.goalGovernance.cost_summary.total_cost_units).toBe(3.0);
    expect(cmp.goalCostTasks().map((task: any) => task.id)).toEqual(['task-1', 'task-2']);
    expect(cmp.goalReportingLoading).toBe(false);
  });
});
