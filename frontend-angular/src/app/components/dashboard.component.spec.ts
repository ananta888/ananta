import { of, throwError } from 'rxjs';

import { DashboardComponent } from './dashboard.component.ts';
import { DashboardFacade } from './dashboard.facade.ts';

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
    getDemoPreview: vi.fn(() => of({ isolated: true, examples: [] })),
    planGoal: vi.fn(() => of({ created_task_ids: [], goal_id: null })),
    createGoal: vi.fn(() => of({ created_task_ids: ['T-1'], goal: { id: 'G-1' } })),
    tasks: vi.fn(() => []),
    tasksLoading: vi.fn(() => false),
    tasksLastLoadedAt: vi.fn(() => 1739790000),
    taskCollectionError: vi.fn(() => null),
    connectTaskCollection: vi.fn(),
    disconnectTaskCollection: vi.fn(),
    listInstructionProfiles: vi.fn(() => of([])),
    listInstructionOverlays: vi.fn(() => of([])),
  };

  function createFacade(): DashboardFacade {
    const facade = Object.create(DashboardFacade.prototype) as DashboardFacade & {
      hubApi: any;
      taskFacade: any;
      ns: any;
      toast: any;
    };
    facade.viewState = { loading: true, error: null, empty: false };
    facade.stats = null;
    facade.systemHealth = null;
    facade.contracts = null;
    facade.history = [];
    facade.agents = [];
    facade.teams = [];
    facade.activeTeam = null;
    facade.roles = [];
    facade.benchmarkData = [];
    facade.benchmarkUpdatedAt = null;
    facade.benchmarkRecommendation = null;
    facade.llmDefaults = null;
    facade.llmExplicitOverride = null;
    facade.llmEffectiveRuntime = null;
    facade.hubCopilotStatus = null;
    facade.contextPolicyStatus = null;
    facade.artifactFlowStatus = null;
    facade.researchBackendStatus = null;
    facade.runtimeTelemetry = null;
    facade.taskTimeline = [];
    facade.benchmarkTaskKind = 'analysis';
    (facade as any).readModelInFlight = false;
    facade.hubApi = hubApiMock;
    facade.taskFacade = hubApiMock;
    facade.ns = { error: vi.fn() };
    return facade;
  }

  function createComponent(): DashboardComponent {
    const cmp = Object.create(DashboardComponent.prototype) as DashboardComponent & {
      hubApi: any;
      liveState: any;
      facade: DashboardFacade;
      taskFacade: any;
      ns: any;
      goalReporting: any;
      workspaceViewModel: any;
    };
    cmp.facade = createFacade();
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.benchmarkTaskKind = 'analysis';
    cmp.hubApi = hubApiMock;
    cmp.liveState = { ensureSystemEvents: vi.fn(), systemStreamConnected: () => false, lastSystemEvent: () => null };
    cmp.taskFacade = hubApiMock;
    cmp.ns = { error: vi.fn() } as any;
    cmp.toast = { success: vi.fn(), error: vi.fn() } as any;
    cmp.goalReporting = {
      state: { goals: [], selectedGoalId: '', goalDetail: null, goalGovernance: null, loading: false },
      refresh: vi.fn(),
      recentGoals: vi.fn((n: number) => (cmp.goalReporting.state.goals || []).slice(0, n)),
      activeGoalCount: vi.fn(() => (cmp.goalReporting.state.goals || []).filter((g: any) => g.status !== 'completed').length),
      costTasks: vi.fn(() => []),
    };
    cmp.workspaceViewModel = {
      nextTaskCount: (tasks: any[]) => (Array.isArray(tasks) ? tasks.filter(t => String(t?.status || '').toLowerCase() === 'todo').length : 0),
      starterProgress: (_: any) => ({ done: 2, total: 3, label: 'stub' }),
    };
    cmp.showFirstStartWizard = true;
    cmp.showAdvancedDashboard = false;
    cmp.instructionProfiles = [];
    cmp.instructionOverlays = [];
    cmp.selectedInstructionProfileId = '';
    cmp.selectedInstructionOverlayId = '';
    cmp.firstStartOptions = [
      { id: 'new-software-project', title: 'Neues Projekt anlegen', description: 'Aus einer Idee Blueprint, Backlog und erste pruefbare Tasks erzeugen.' },
      { id: 'research-evolution', title: 'Mit Research starten', description: 'Ein bestehendes Projekt ueber Recherche, Proposal und Review weiterentwickeln.' },
      { id: 'project-evolution', title: 'Projekt weiterentwickeln', description: 'Ein bestehendes Projekt in kleinen reviewbaren Schritten veraendern.' },
      { id: 'demo', title: 'Demo ansehen', description: 'Beispiele lesen und kontrolliert als echte Ziele starten.' },
    ];
    cmp.hiddenHints = new Set();
    return cmp;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
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
          artifact_flow: { effective: { enabled: true, rag_enabled: true, rag_top_k: 4 } },
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
    cmp.facade.refresh('http://hub:5000', 'coding');

    expect(hubApiMock.getDashboardReadModel).toHaveBeenCalledWith('http://hub:5000', { benchmarkTaskKind: 'coding' });
    expect(cmp.benchmarkTaskKind).toBe('coding');
    expect(cmp.benchmarkData[0].id).toBe('codex:gpt-5-codex');
    expect(cmp.benchmarkUpdatedAt).toBe(1739790000);
    expect(cmp.benchmarkRecommendation?.recommended?.selection_source).toBe('benchmarks_available_top_ranked');
    expect(cmp.llmEffectiveRuntime?.benchmark_applied).toBe(true);
    expect(cmp.hubCopilotStatus?.active).toBe(true);
    expect(cmp.contextPolicyStatus?.effective?.mode).toBe('standard');
    expect(cmp.artifactFlowStatus?.effective?.rag_top_k).toBe(4);
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

  it('loads isolated demo preview through the control-plane facade', () => {
    hubApiMock.getDemoPreview.mockReturnValue(of({
      isolated: true,
      examples: [{ id: 'repo-analysis', title: 'Repository verstehen', goal: 'Analyse', outcome: 'Plan', tasks: ['Lesen'] }],
    }));

    const cmp = createComponent();
    cmp.loadDemoPreview();

    expect(hubApiMock.getDemoPreview).toHaveBeenCalledWith('http://hub:5000');
    expect(cmp.demoPreview?.examples?.[0].id).toBe('repo-analysis');
    expect(cmp.demoLoading).toBe(false);
    expect(cmp.demoError).toBe('');
  });

  it('starts demo examples through the normal hub goal planning path', () => {
    hubApiMock.planGoal = vi.fn(() => of({ created_task_ids: ['T-1', 'T-2'], goal_id: 'G-1' }));
    const cmp = createComponent();

    cmp.startDemoExample({
      id: 'repo-analysis',
      title: 'Repository verstehen',
      goal: 'Analysiere ein Repository',
      outcome: 'Plan',
      tasks: ['Lesen'],
      starter_context: 'Demo-Kontext',
    });

    expect(hubApiMock.planGoal).toHaveBeenCalledWith('http://hub:5000', {
      goal: 'Analysiere ein Repository',
      context: 'Demo-Kontext',
      create_tasks: true,
    });
    expect(cmp.quickGoalResult?.tasks_created).toBe(2);
    expect(cmp.showFirstStartWizard).toBe(false);
  });

  it('marks first-start wizard as complete before opening demo preview', () => {
    const cmp = createComponent();
    cmp.loadDemoPreview = vi.fn();

    cmp.chooseFirstStart('demo');

    expect(cmp.showFirstStartWizard).toBe(false);
    expect(cmp.loadDemoPreview).toHaveBeenCalled();
  });

  it('uses new project as the obvious first-start path', () => {
    const cmp = createComponent();
    cmp.focusQuickGoal = vi.fn();

    expect(cmp.firstStartOptions.map(option => option.id)).toEqual([
      'new-software-project',
      'research-evolution',
      'project-evolution',
      'demo',
    ]);

    cmp.chooseFirstStart('new-software-project');

    expect(cmp.showFirstStartWizard).toBe(false);
    expect(cmp.selectedPresetId).toBe('new-software-project');
    expect(cmp.quickGoalText).toContain('Lege ein neues Softwareprojekt');
    expect(cmp.currentQuickGoalExpectation()?.expectedResult).toContain('Projekt-Blueprint');
    expect(cmp.focusQuickGoal).toHaveBeenCalled();
  });

  it('applies goal presets to the quick goal form', () => {
    const cmp = createComponent();
    cmp.focusQuickGoal = vi.fn();

    cmp.applyGoalPreset({
      id: 'bugfix-plan',
      title: 'Bugfix planen',
      goal: 'Plane einen Bugfix',
      outcome: 'Plan',
      tasks: ['Reproduzieren'],
      starter_context: 'Kontext',
    });

    expect(cmp.quickGoalText).toBe('Plane einen Bugfix');
    expect(cmp.quickGoalContext).toBe('Kontext');
    expect(cmp.selectedPresetId).toBe('bugfix-plan');
    expect(cmp.currentQuickGoalExpectation()?.expectedResult).toContain('Regressionstest');
    expect(cmp.focusQuickGoal).toHaveBeenCalled();
  });

  it('offers the research-evolution path as a visible first-start and preset option', () => {
    const cmp = createComponent();
    cmp.focusQuickGoal = vi.fn();

    cmp.chooseFirstStart('research-evolution');

    expect(cmp.selectedPresetId).toBe('research-evolution');
    expect(cmp.quickGoalText).toContain('recherchiere zuerst relevante Quellen');
    expect(cmp.currentQuickGoalExpectation()?.nextAction).toContain('Research-Artefakt pruefen');
    expect(cmp.focusQuickGoal).toHaveBeenCalled();
  });

  it('clears preset expectation when the quick goal text is edited manually', () => {
    const cmp = createComponent();
    cmp.focusQuickGoal = vi.fn();

    cmp.applyGoalPreset({
      id: 'change-review',
      title: 'Change Review',
      goal: 'Review changes',
      outcome: 'Findings',
      tasks: ['Review'],
    });
    cmp.updateQuickGoalText('Bitte nur den Frontend-Teil bewerten');

    expect(cmp.quickGoalText).toBe('Bitte nur den Frontend-Teil bewerten');
    expect(cmp.selectedPresetId).toBe('');
  });

  it('turns quick goal failures into actionable user-facing messages', () => {
    hubApiMock.planGoal = vi.fn(() => throwError(() => ({ message: 'policy blocked' })));
    const cmp = createComponent();
    cmp.quickGoalText = 'Plane ein riskantes Ziel';

    cmp.submitQuickGoal();

    expect(cmp.quickGoalError).toContain('Sicherheitsregel');
    expect(cmp.quickGoalBusy).toBe(false);
  });

  it('submits guided goals with wizard metadata defaults', () => {
    hubApiMock.createGoal = vi.fn(() => of({ created_task_ids: ['T-1'], goal: { id: 'G-1' } }));
    const cmp = createComponent();
    cmp.refresh = vi.fn();

    cmp.submitGuidedGoal({
      mode: { id: 'repo' } as any,
      modeData: { goal: 'Analysiere das Repository', context: 'Nur Frontend' },
    } as any);

    expect(hubApiMock.createGoal).toHaveBeenCalledWith('http://hub:5000', {
      mode: 'repo',
      mode_data: {
        goal: 'Analysiere das Repository',
        context: 'Nur Frontend',
        wizard: {
          execution_depth: 'standard',
          safety_level: 'balanced',
          context: 'Nur Frontend',
        },
      },
      create_tasks: true,
    });
  });

  it('submits guided goals with wizard metadata', () => {
    hubApiMock.createGoal = vi.fn(() => of({ created_task_ids: ['T-1', 'T-2'], goal: { id: 'G-42' } }));
    const cmp = createComponent();
    cmp.refresh = vi.fn();
    cmp.submitGuidedGoal({
      mode: { id: 'repo' } as any,
      modeData: {
        goal: 'Analysiere das Repository',
        context: 'Nur Frontend',
        execution_depth: 'deep',
        safety_level: 'safe',
      },
    } as any);

    expect(hubApiMock.createGoal).toHaveBeenCalledWith('http://hub:5000', {
      mode: 'repo',
      mode_data: {
        goal: 'Analysiere das Repository',
        context: 'Nur Frontend',
        execution_depth: 'deep',
        safety_level: 'safe',
        wizard: {
          execution_depth: 'deep',
          safety_level: 'safe',
          context: 'Nur Frontend',
        },
      },
      create_tasks: true,
    });
    expect(cmp.quickGoalResult?.goal_id).toBe('G-42');
  });

  it('summarizes personal home progress from goals and tasks', () => {
    const cmp = createComponent();
    cmp.showFirstStartWizard = false;
    cmp.goalReporting.state.goals = [
      { id: 'G-1', goal: 'Open', status: 'running' } as any,
      { id: 'G-2', goal: 'Done', status: 'completed' } as any,
    ];
    hubApiMock.tasks.mockReturnValueOnce([
      { id: 'T-1', status: 'todo' },
      { id: 'T-2', status: 'completed' },
    ]);

    expect(cmp.recentGoals().map(goal => goal.id)).toEqual(['G-1', 'G-2']);
    expect(cmp.activeGoalCount()).toBe(1);
    expect(cmp.nextTaskCount()).toBe(1);
    expect(cmp.starterProgress().done).toBeGreaterThanOrEqual(2);
  });

  it('builds shared display view models for dashboard summaries', () => {
    const cmp = createComponent();
    cmp.stats = {
      agents: { total: 3, online: 2, offline: 1 },
      tasks: { total: 5, completed: 3, failed: 1, in_progress: 1 },
    } as any;
    cmp.systemHealth = { status: 'degraded' } as any;

    expect(cmp.agentSummaryItems()).toEqual([
      { label: 'Gesamt', value: 3 },
      { label: 'Online', value: 2 },
      { label: 'Offline', value: 1 },
    ]);
    expect(cmp.taskSummaryItems()[1]).toEqual({ label: 'Abgeschlossen', value: 3 });
    expect(cmp.systemStatusLabel()).toBe('degraded');
    expect(cmp.systemStatusTone()).toBe('warning');
  });

  it('persists dismissed inline hints', () => {
    const cmp = createComponent();

    expect(cmp.isHintVisible('quick-goal')).toBe(true);
    cmp.dismissHint('quick-goal');

    expect(cmp.isHintVisible('quick-goal')).toBe(false);
    expect(localStorage.getItem('ananta.hidden-hints')).toContain('quick-goal');
  });

  it('derives active inference runtime tile data from telemetry', () => {
    const cmp = createComponent();
    cmp.llmEffectiveRuntime = { provider: 'ollama', model: 'glm-4.7', temperature: 0.35 };
    cmp.runtimeTelemetry = {
      providers: {
        ollama: {
          status: 'ok',
          reachable: true,
          candidate_count: 2,
          models: [{ name: 'glm-4.7', details: { num_ctx: 8192 } }],
          activity: {
            gpu_active: true,
            executor_summary: { gpu: 1, cpu: 0, unknown: 0 },
            active_models: [{ name: 'glm-4.7', executor: 'gpu' }],
          },
        },
      },
    };

    const tile = cmp.activeInferenceRuntime();
    expect(tile).not.toBeNull();
    expect(tile.provider).toBe('ollama');
    expect(tile.model).toBe('glm-4.7');
    expect(tile.contextLengthLabel).toContain('8192');
    expect(tile.gpuActiveLabel).toBe('yes');
    expect(tile.executorLabel).toBe('GPU');
    expect(tile.temperatureLabel).toBe('0.35');
  });

  it('builds live runtime model rows from ollama and lmstudio telemetry', () => {
    const cmp = createComponent();
    cmp.llmEffectiveRuntime = { provider: 'ollama', model: 'glm-4.7' };
    cmp.runtimeTelemetry = {
      providers: {
        ollama: {
          models: [{ name: 'glm-4.7', details: { num_ctx: 8192 } }],
          activity: {
            active_models: [{ name: 'glm-4.7', executor: 'gpu' }],
          },
        },
        lmstudio: {
          candidates: [
            { id: 'qwen2.5-coder', context_length: 16384, loaded: true },
          ],
        },
      },
    };

    const rows = cmp.liveRuntimeModels();
    expect(rows).toHaveLength(2);
    expect(rows[0].provider).toBe('ollama');
    expect(rows[0].statusLabel).toBe('active runtime');
    expect(rows[0].executorLabel).toBe('GPU');
    expect(rows[0].contextLengthLabel).toContain('8192');

    expect(rows[1].provider).toBe('lmstudio');
    expect(rows[1].model).toBe('qwen2.5-coder');
    expect(rows[1].statusLabel).toBe('loaded');
    expect(rows[1].contextLengthLabel).toContain('16384');
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

    expect(cmp.goalReporting.refresh).toHaveBeenCalledWith('http://hub:5000', undefined);
  });
});
