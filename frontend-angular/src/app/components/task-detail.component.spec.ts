import { of } from 'rxjs';

import { TaskDetailComponent } from './task-detail.component.ts';

describe('TaskDetailComponent', () => {
  function createComponent(): TaskDetailComponent {
    const cmp = Object.create(TaskDetailComponent.prototype) as TaskDetailComponent & { taskFacade: any; ns: any; hub: any };
    const proto = TaskDetailComponent.prototype as any;
    for (const methodName of [
      'workerContextText',
      'allowedTools',
      'expectedSchema',
      'routingDecision',
      'routingRequiredCapabilities',
      'routingMatchedCapabilities',
      'researchSources',
      'researchCitations',
      'researchVerification',
      'researchBackendMetadata',
      'sourceCatalogEntries',
      'sourceCatalogStatusClass',
      'sourceCatalogFailedCitationRefs',
      'sourceCatalogFailedReasonFor',
      'provenanceEvents',
      'latestExecutionCostSummary',
      'taskSessionReuseLabel',
      'reviewProposal',
      'taskLiveTerminalConnection',
      'taskDiagnosticTerminalConnection',
      'selectedTaskTerminalConnection',
      'taskLiveTerminalLink',
      'statusExplanation',
      'taskSafetyNotice',
    ]) {
      if (typeof proto[methodName] === 'function') {
        (cmp as any)[methodName] = proto[methodName].bind(cmp);
      }
    }
    Object.defineProperty(cmp, 'taskFacade', {
      value: { reviewTaskProposal: vi.fn(() => of({})) },
      configurable: true,
      writable: true,
    });
    Object.defineProperty(cmp, 'ns', {
      value: { success: vi.fn(), error: vi.fn(), fromApiError: vi.fn((_e: any, fallback: string) => fallback) },
      configurable: true,
      writable: true,
    });
    cmp.hub = { url: 'http://hub:5000' };
    cmp.task = {
      worker_execution_context: {
        context: { context_text: 'Use repo context', chunks: [{ id: 'c1' }] },
        allowed_tools: ['codex', 'sgpt'],
        expected_output_schema: { type: 'object', required: ['summary'] },
        routing: {
          strategy: 'capability_quality_load_match',
          task_kind: 'research',
          required_capabilities: ['research', 'repo_research'],
          matched_capabilities: ['research', 'repo_research'],
        },
      },
      last_proposal: {
        review: { required: true, status: 'pending' },
        research_artifact: {
          sources: [{ url: 'https://example.com', title: 'Example' }],
          citations: [{ url: 'https://example.com', label: 'Example Citation', excerpt: 'Snippet' }],
          verification: { ready: true, has_sources: true, source_count: 1 },
          backend_metadata: { backend: 'deerflow', source_count: 1 },
        },
      },
      history: [
        { event_type: 'proposal_result', timestamp: 1, reason: 'planned' },
        { event_type: 'execution_result', timestamp: 2, reason: 'done', cost_summary: { cost_units: 1.25, tokens_total: 640, latency_ms: 1200, provider: 'openai', model: 'gpt-4o-mini' } },
        { event_type: 'other', timestamp: 3 },
      ],
    };
    cmp.busy = false;
    cmp.taskSourcesPayload = {
      source_catalog_id: 'catalog-1',
      sources: [
        { source_id: 'SRC_0001', source_type: 'repo_file', path: 'README.md', allowed_for_llm_scope: true },
        { source_id: 'RUN_0001', source_type: 'tool_run', path: 'python mining_demo.py', allowed_for_llm_scope: true },
      ],
    };
    cmp.taskAnswerVerificationPayload = {
      status: 'verified',
      verified_claim_count: 2,
      unverified_claim_count: 0,
      failed_claims: [],
    };
    return cmp;
  }

  it('exposes worker context, schema and provenance helpers', () => {
    const cmp = createComponent();

    expect(cmp.workerContextText()).toBe('Use repo context');
    expect(cmp.allowedTools()).toEqual(['codex', 'sgpt']);
    expect(cmp.expectedSchema()).toEqual({ type: 'object', required: ['summary'] });
    expect(cmp.routingRequiredCapabilities()).toEqual(['research', 'repo_research']);
    expect(cmp.routingMatchedCapabilities()).toEqual(['research', 'repo_research']);
    expect(cmp.researchSources().length).toBe(1);
    expect(cmp.researchCitations().length).toBe(1);
    expect(cmp.researchVerification()).toEqual({ ready: true, has_sources: true, source_count: 1 });
    expect(cmp.researchBackendMetadata()).toEqual({ backend: 'deerflow', source_count: 1 });
    expect(cmp.sourceCatalogEntries().length).toBe(2);
    expect(cmp.sourceCatalogStatusClass()).toBe('success');
    expect(cmp.provenanceEvents().length).toBe(2);
  });

  it('marks failed citation refs and exposes deterministic failure reason', () => {
    const cmp = createComponent();
    cmp.taskAnswerVerificationPayload = {
      status: 'failed_unknown_source',
      verified_claim_count: 1,
      unverified_claim_count: 1,
      failed_claims: [
        {
          claim_id: 'CLM_0009',
          reason: 'failed_unknown_source',
          citation_refs: ['SRC_9999'],
        },
      ],
    };

    expect(cmp.sourceCatalogStatusClass()).toBe('danger');
    expect(cmp.sourceCatalogFailedCitationRefs()).toEqual(['SRC_9999']);
    expect(cmp.sourceCatalogFailedReasonFor('SRC_9999')).toBe('failed_unknown_source');
  });

  it('reviews proposals through hub api and reloads task', () => {
    const cmp = createComponent();
    cmp.reload = vi.fn();
    Object.defineProperty(cmp, 'tid', { value: 'T-1', configurable: true });

    cmp.reviewProposal('approve');

    expect(cmp.taskFacade.reviewTaskProposal).toHaveBeenCalledWith('http://hub:5000', 'T-1', { action: 'approve' });
    expect(cmp.ns.success).toHaveBeenCalledWith('Vorschlag freigegeben');
    expect(cmp.reload).toHaveBeenCalled();
  });

  it('resolves embedded live terminal connection metadata from task verification', () => {
    const cmp = createComponent();
    cmp.allAgents = [
      { name: 'alpha', role: 'worker', url: 'http://alpha:5000' },
      { name: 'hub', role: 'hub', url: 'http://hub:5000' },
    ] as any;
    cmp.task = {
      verification_status: {
        opencode_live_terminal: {
          agent_url: 'http://alpha:5000',
          forward_param: 'cli-forward-1',
        },
      },
    };

    expect(cmp.taskLiveTerminalConnection()).toEqual({
      kind: 'task',
      displayName: 'alpha',
      panelAgentName: 'alpha',
      agentUrl: 'http://alpha:5000',
      forwardParam: 'cli-forward-1',
      token: undefined,
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
        forward_param: 'cli-forward-1',
      },
    });
    expect(cmp.taskLiveTerminalLink()).toEqual({
      agentName: 'alpha',
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
        forward_param: 'cli-forward-1',
      },
    });
  });

  it('keeps the embedded live terminal available when the worker url is not in the directory', () => {
    const cmp = createComponent();
    cmp.allAgents = [{ name: 'alpha', role: 'worker', url: 'http://alpha:5000' }] as any;
    cmp.task = {
      verification_status: {
        cli_session: {
          agent_url: 'http://ai-agent-beta:5000',
          forward_param: 'cli-forward-2',
        },
      },
    };

    expect(cmp.taskLiveTerminalConnection()).toEqual({
      kind: 'task',
      displayName: 'ai-agent-beta',
      panelAgentName: undefined,
      agentUrl: 'http://ai-agent-beta:5000',
      forwardParam: 'cli-forward-2',
      token: undefined,
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
        forward_param: 'cli-forward-2',
      },
    });
    expect(cmp.taskLiveTerminalLink()).toBeNull();
  });

  it('builds a direct worker diagnosis terminal without a forward parameter', () => {
    const cmp = createComponent();
    cmp.allAgents = [
      { name: 'alpha', role: 'worker', url: 'http://alpha:5000', token: 'worker-token' },
    ] as any;
    cmp.task = {
      assigned_agent_url: 'http://alpha:5000',
    };

    expect(cmp.taskDiagnosticTerminalConnection()).toEqual({
      kind: 'diagnostic',
      displayName: 'alpha',
      panelAgentName: 'alpha',
      agentUrl: 'http://alpha:5000',
      token: 'worker-token',
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
      },
    });
  });

  it('switches the worker panel link to diagnosis mode when selected', () => {
    const cmp = createComponent();
    cmp.allAgents = [
      { name: 'alpha', role: 'worker', url: 'http://alpha:5000', token: 'worker-token' },
    ] as any;
    cmp.task = {
      assigned_agent_url: 'http://alpha:5000',
      verification_status: {
        opencode_live_terminal: {
          agent_url: 'http://alpha:5000',
          forward_param: 'cli-forward-1',
        },
      },
    };
    cmp.taskTerminalMode = 'diagnostic';

    expect(cmp.selectedTaskTerminalConnection()).toEqual({
      kind: 'diagnostic',
      displayName: 'alpha',
      panelAgentName: 'alpha',
      agentUrl: 'http://alpha:5000',
      token: 'worker-token',
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
      },
    });
    expect(cmp.taskLiveTerminalLink()).toEqual({
      agentName: 'alpha',
      queryParams: {
        tab: 'terminal',
        mode: 'interactive',
      },
    });
  });

  it('renders explicit session reuse values', () => {
    const cmp = createComponent();
    cmp.task = {
      last_proposal: { routing: { session_mode: 'stateful', session_reused: true } },
    };
    expect(cmp.taskSessionReuseLabel()).toBe('ja');

    cmp.task = {
      last_proposal: { routing: { session_mode: 'stateful', session_reused: false } },
    };
    expect(cmp.taskSessionReuseLabel()).toBe('nein');
  });

  it('renders unknown session reuse when stateful without explicit flag', () => {
    const cmp = createComponent();
    cmp.task = {
      last_proposal: { routing: { session_mode: 'stateful' } },
    };
    expect(cmp.taskSessionReuseLabel()).toBe('unbekannt');
  });

  it('explains blocked and review-required tasks in user-facing language', () => {
    const cmp = createComponent();
    cmp.task = { status: 'blocked' };

    expect(cmp.statusExplanation('blocked')).toContain('wartet bewusst');
    expect(cmp.taskSafetyNotice()).toEqual({
      title: 'Wartet auf Klaerung',
      body: 'Diese Aufgabe wartet bewusst. Pruefe Kontext, Review oder fehlende Eingaben, bevor sie weiterlaeuft.',
    });

    cmp.task = { status: 'todo', last_proposal: { review: { required: true, status: 'pending' } } };
    expect(cmp.taskSafetyNotice()?.title).toBe('Freigabe erforderlich');
  });
});
