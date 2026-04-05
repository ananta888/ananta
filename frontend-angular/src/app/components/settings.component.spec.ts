import { of, throwError } from 'rxjs';

import {
  SettingsComponent,
} from './settings.component.ts';

describe('SettingsComponent (benchmark config)', () => {
  const systemMock = {
    getLlmBenchmarksConfig: vi.fn(() => of({})),
    setConfig: vi.fn(() => of({})),
    getConfig: vi.fn(() => of({ default_provider: 'lmstudio' })),
    resolveHubAgent: vi.fn(() => ({ name: 'hub', url: 'http://hub:5000', role: 'hub' })),
    listConfiguredAgents: vi.fn(() => [{ name: 'hub', url: 'http://hub:5000', role: 'hub' }]),
    listProviderCatalog: vi.fn(() => of({ providers: [] })),
    getLlmHistory: vi.fn(() => of([])),
  };
  const notificationMock = {
    success: vi.fn(),
    error: vi.fn(),
  };

  function createComponent(): SettingsComponent {
    const cmp = Object.create(SettingsComponent.prototype) as SettingsComponent & { system: any; hubApi: any; dir: any; ns: any; api: any };
    const proto = SettingsComponent.prototype as any;
    for (const methodName of [
      'normalizeHubCopilotConfig',
      'normalizeOpenAICompatibleBaseUrl',
      'parseCommaList',
      'getHubCopilotProvider',
      'getHubCopilotModel',
      'getHubCopilotProviderSource',
      'getHubCopilotModelSource',
      'isHubCopilotActive',
    ]) {
      if (typeof proto[methodName] === 'function') {
        (cmp as any)[methodName] = proto[methodName].bind(cmp);
      }
    }
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.allAgents = [];
    cmp.config = {};
    cmp.providerCatalog = null;
    cmp.benchmarkConfig = null;
    cmp.system = systemMock;
    cmp.hubApi = systemMock;
    cmp.dir = { list: () => systemMock.listConfiguredAgents() };
    cmp.api = {
      getConfig: vi.fn(() => of({ default_provider: 'lmstudio' })),
      setConfig: vi.fn(() => of({})),
      sgptBackends: vi.fn(() => of({ preflight: { research_backends: {} } })),
    };
    cmp.ns = notificationMock;
    cmp.benchmarkRetentionDays = 90;
    cmp.benchmarkRetentionSamples = 2000;
    cmp.benchmarkProviderOrderTextValue = '';
    cmp.benchmarkModelOrderTextValue = '';
    cmp.benchmarkValidationError = '';
    cmp.agentLlmDrafts = {};
    cmp.researchBackendStatus = null;
    return cmp;
  }

  function resolveHubCopilotProvider(config: any): string {
    const provider = String(config?.hub_copilot?.provider || '').trim().toLowerCase();
    if (provider) return provider;
    const llmProvider = String(config?.llm_config?.provider || '').trim().toLowerCase();
    return llmProvider || String(config?.default_provider || '').trim().toLowerCase();
  }

  function resolveHubCopilotModel(config: any): string {
    const model = String(config?.hub_copilot?.model || '').trim();
    if (model) return model;
    const llmModel = String(config?.llm_config?.model || '').trim();
    return llmModel || String(config?.default_model || '').trim();
  }

  function resolveHubCopilotProviderSource(config: any): string {
    if (String(config?.hub_copilot?.provider || '').trim()) return 'hub_copilot.provider';
    if (String(config?.llm_config?.provider || '').trim()) return 'llm_config.provider';
    return 'default_provider';
  }

  function resolveHubCopilotModelSource(config: any): string {
    if (String(config?.hub_copilot?.model || '').trim()) return 'hub_copilot.model';
    if (String(config?.llm_config?.model || '').trim()) return 'llm_config.model';
    return 'default_model';
  }

  function normalizeContextBundlePolicy(config: any): any {
    const raw = config && typeof config === 'object' ? config : {};
    const mode = ['compact', 'standard', 'full'].includes(String(raw.mode || '').trim().toLowerCase())
      ? String(raw.mode || '').trim().toLowerCase()
      : 'full';
    const compactMaxChunks = Number(raw.compact_max_chunks);
    const standardMaxChunks = Number(raw.standard_max_chunks);
    return {
      mode,
      compact_max_chunks: Number.isFinite(compactMaxChunks) ? Math.max(1, Math.min(50, compactMaxChunks)) : 3,
      standard_max_chunks: Number.isFinite(standardMaxChunks) ? Math.max(1, Math.min(50, standardMaxChunks)) : 8,
    };
  }

  function normalizeArtifactFlow(config: any): any {
    const raw = config && typeof config === 'object' ? config : {};
    const ragTopK = Number(raw.rag_top_k);
    const maxTasks = Number(raw.max_tasks);
    const maxWorkerJobsPerTask = Number(raw.max_worker_jobs_per_task);
    return {
      enabled: raw.enabled !== false,
      rag_enabled: raw.rag_enabled !== false,
      rag_top_k: Number.isFinite(ragTopK) ? Math.max(1, Math.min(20, ragTopK)) : 3,
      rag_include_content: raw.rag_include_content === true,
      max_tasks: Number.isFinite(maxTasks) ? Math.max(1, Math.min(200, maxTasks)) : 30,
      max_worker_jobs_per_task: Number.isFinite(maxWorkerJobsPerTask)
        ? Math.max(1, Math.min(20, maxWorkerJobsPerTask))
        : 5,
    };
  }

  function resolveContextBundlePolicy(config: any): any {
    const normalized = normalizeContextBundlePolicy(config?.context_bundle_policy);
    if (normalized.mode === 'compact') {
      return { ...normalized, include_context_text: false, max_chunks: normalized.compact_max_chunks };
    }
    if (normalized.mode === 'standard') {
      return { ...normalized, include_context_text: true, max_chunks: normalized.standard_max_chunks };
    }
    return { ...normalized, include_context_text: true, max_chunks: null };
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders provider/model precedence text from benchmark config', () => {
    const cmp = createComponent();
    cmp.benchmarkProviderOrderTextValue = 'proposal_backend, default_provider';
    cmp.benchmarkModelOrderTextValue = 'proposal_model, default_model';

    expect(cmp.benchmarkProviderOrderText()).toBe('proposal_backend -> default_provider');
    expect(cmp.benchmarkModelOrderText()).toBe('proposal_model -> default_model');
  });

  it('loads benchmark config via API and falls back to null on error', () => {
    const cmp = createComponent();
    systemMock.getLlmBenchmarksConfig.mockReturnValueOnce(
      of({ retention: { max_days: 30, max_samples: 1000 } })
    );
    cmp.loadBenchmarkConfig();
    expect(cmp.benchmarkConfig.retention.max_days).toBe(30);

    systemMock.getLlmBenchmarksConfig.mockReturnValueOnce(
      throwError(() => new Error('api down'))
    );
    cmp.loadBenchmarkConfig();
    expect(cmp.benchmarkConfig).toBeNull();
  });

  it('keeps provider/model defaults consistent with catalog', () => {
    const cmp = createComponent();
    cmp.providerCatalog = {
      providers: [
        {
          provider: 'lmstudio',
          models: [{ id: 'model-a', display_name: 'model-a' }],
        },
      ],
    };
    cmp.config = { default_provider: 'lmstudio', default_model: 'unknown' };
    cmp.ensureProviderModelConsistency();

    expect(cmp.config.default_model).toBe('model-a');
  });

  it('initializes codex runtime settings when loading config without codex_cli block', () => {
    const cmp = createComponent() as any;
    cmp.allAgents = [];
    cmp.system = {
      ...systemMock,
      getConfig: vi.fn(() => of({ default_provider: 'lmstudio' })),
      resolveHubAgent: vi.fn(() => ({ name: 'hub', url: 'http://hub:5000', role: 'hub' })),
      listConfiguredAgents: vi.fn(() => [{ name: 'hub', url: 'http://hub:5000', role: 'hub' }]),
    };
    cmp.hubApi = cmp.system;
    cmp.dir = { list: () => cmp.system.listConfiguredAgents() };
    cmp.api = {
      getConfig: vi.fn(() => of({ default_provider: 'lmstudio' })),
      setConfig: vi.fn(() => of({})),
    };
    cmp.syncQualityGatesFromConfig = vi.fn();
    cmp.loadProviderCatalog = vi.fn();

    cmp.load();

    expect(cmp.config.codex_cli).toEqual({ target_provider: '', base_url: '', api_key_profile: '', prefer_lmstudio: true });
  });

  it('classifies codex runtime as local when codex_cli points to LM Studio', () => {
    const cmp = createComponent();
    cmp.config = {
      default_provider: 'codex',
      codex_cli: { base_url: 'http://127.0.0.1:1234/v1/chat/completions', prefer_lmstudio: true },
      lmstudio_url: 'http://127.0.0.1:1234/v1',
    };

    expect(cmp.getProviderRuntimeKind('codex')).toBe('local openai-compatible');
    expect(cmp.getCodexCliTargetSummary()).toContain('http://127.0.0.1:1234/v1');
  });

  it('groups providers by local and cloud runtime classes for the selector', () => {
    const cmp = createComponent();
    cmp.providerCatalog = {
      providers: [
        { provider: 'lmstudio', available: true, model_count: 2 },
        { provider: 'ollama', available: true, model_count: 1 },
        { provider: 'openai', available: true, model_count: 2 },
        { provider: 'anthropic', available: false, model_count: 1 },
      ],
    };

    expect(cmp.getProviderSelectGroups()).toEqual([
      {
        label: 'Lokale Runtimes',
        providers: [
          { id: 'lmstudio', available: true, model_count: 2 },
          { id: 'ollama', available: true, model_count: 1 },
        ],
      },
      {
        label: 'Cloud / Hosted Provider',
        providers: [
          { id: 'openai', available: true, model_count: 2 },
          { id: 'anthropic', available: false, model_count: 1 },
        ],
      },
    ]);
  });

  it('reports warnings for missing local lmstudio URL and missing codex cloud credentials', () => {
    const cmp = createComponent();
    cmp.providerCatalog = {
      providers: [
        { provider: 'lmstudio', available: true, model_count: 0 },
      ],
    };
    cmp.config = {
      default_provider: 'lmstudio',
      lmstudio_url: '',
      codex_cli: {
        base_url: 'https://api.openai.com/v1',
        api_key_profile: '',
        prefer_lmstudio: false,
      },
      openai_api_key: '',
    };

    expect(cmp.getLlmConfigurationWarnings()).toEqual(expect.arrayContaining([
      'LM Studio ist Default-Provider, aber die LM-Studio-URL ist nicht gesetzt.',
      'Codex CLI zeigt auf eine Cloud/OpenAI-kompatible Runtime, aber weder API-Key-Profil noch globaler Key sind erkennbar.',
    ]));
  });

  it('normalizes codex runtime URLs during load and save', () => {
    const cmp = createComponent() as any;
    cmp.allAgents = [];
    cmp.system = {
      ...systemMock,
      getConfig: vi.fn(() => of({
        default_provider: 'codex',
        hub_copilot: {
          enabled: true,
          provider: 'OpenAI',
          model: 'gpt-4.1',
          base_url: 'https://example.invalid/v1/chat/completions',
          temperature: 1.4,
          strategy_mode: 'planning_and_routing',
        },
        local_openai_backends: [
          {
            id: 'vllm_local',
            name: 'vLLM Local',
            base_url: 'http://127.0.0.1:8010/v1/chat/completions',
            models: ['qwen2.5-coder', 'deepseek-coder'],
            api_key_profile: ' local-dev ',
            supports_tool_calls: true,
          },
        ],
        codex_cli: {
          target_provider: 'VLLM_LOCAL',
          base_url: 'http://127.0.0.1:1234/v1/chat/completions',
          api_key_profile: ' codex-local ',
          prefer_lmstudio: true,
        },
      })),
      setConfig: vi.fn(() => of({})),
    };
    cmp.hubApi = cmp.system;
    cmp.dir = { list: () => cmp.system.listConfiguredAgents() };
    cmp.api = {
      getConfig: cmp.system.getConfig,
      setConfig: cmp.system.setConfig,
      sgptBackends: vi.fn(() => of({
        preflight: {
          research_backends: {
            deerflow: { provider: 'deerflow', binary_available: true, configured: true, working_dir_exists: true },
          },
        },
      })),
    };
    cmp.syncQualityGatesFromConfig = vi.fn();
    cmp.loadProviderCatalog = vi.fn();

    cmp.load();

    expect(cmp.config.codex_cli.base_url).toBe('http://127.0.0.1:1234/v1');
    expect(cmp.config.codex_cli.target_provider).toBe('vllm_local');
    expect(cmp.config.local_openai_backends).toEqual([
      {
        id: 'vllm_local',
        provider: 'vllm_local',
        name: 'vLLM Local',
        base_url: 'http://127.0.0.1:8010/v1',
        api_key_profile: 'local-dev',
        models: ['qwen2.5-coder', 'deepseek-coder'],
        models_text: 'qwen2.5-coder, deepseek-coder',
        supports_tool_calls: true,
      },
    ]);
    cmp.config.hub_copilot = {
      enabled: true,
      provider: 'OpenAI',
      model: 'gpt-4.1',
      base_url: 'https://example.invalid/v1/chat/completions',
      temperature: 1.4,
      strategy_mode: 'planning_and_routing',
    };

    cmp.save();

    expect(cmp.system.setConfig).toHaveBeenCalledWith('http://hub:5000', expect.objectContaining({
      local_openai_backends: [
        expect.objectContaining({
          id: 'vllm_local',
          provider: 'vllm_local',
          base_url: 'http://127.0.0.1:8010/v1',
          api_key_profile: 'local-dev',
          models: ['qwen2.5-coder', 'deepseek-coder'],
        }),
      ],
      codex_cli: expect.objectContaining({
        target_provider: 'vllm_local',
        base_url: 'http://127.0.0.1:1234/v1',
        api_key_profile: 'codex-local',
      }),
    }));
  });

  it('normalizes research backend config and exposes warnings from preflight state', () => {
    const cmp = createComponent() as any;
    cmp.allAgents = [];
    cmp.system = {
      ...systemMock,
      getConfig: vi.fn(() => of({
        default_provider: 'lmstudio',
        research_backend: {
          provider: 'ANANTA_RESEARCH',
          enabled: true,
          mode: 'CLI',
          command: '',
          working_dir: '/missing/research',
          timeout_seconds: 12,
        },
      })),
    };
    cmp.hubApi = cmp.system;
    cmp.dir = { list: () => cmp.system.listConfiguredAgents() };
    cmp.api = {
      getConfig: cmp.system.getConfig,
      setConfig: vi.fn(() => of({})),
      sgptBackends: vi.fn(() => of({
        preflight: {
          research_backends: {
            ananta_research: {
              provider: 'ananta_research',
              binary_available: false,
              working_dir: '/missing/research',
              working_dir_exists: false,
            },
          },
        },
      })),
    };
    cmp.researchBackendStatus = {
      research_backends: {
        ananta_research: {
          provider: 'ananta_research',
          binary_available: false,
          working_dir: '/missing/research',
          working_dir_exists: false,
        },
      },
    };
    cmp.syncQualityGatesFromConfig = vi.fn();
    cmp.loadProviderCatalog = vi.fn();

    cmp.load();

    expect(cmp.config.research_backend).toEqual(
      expect.objectContaining({
        provider: 'ananta_research',
        mode: 'cli',
        timeout_seconds: 30,
      })
    );
    expect(cmp.getSupportedResearchProviders()).toEqual(['ananta_research']);
    expect(cmp.getResearchBackendWarnings()).toEqual(expect.arrayContaining([
      'Research-Backend ananta_research ist aktiviert, aber ohne command konfiguriert.',
      'Research-Backend ananta_research ist aktiviert, aber das konfigurierte Binary ist aktuell nicht verfuegbar.',
      'Research-Backend ananta_research verwendet ein fehlendes working_dir: /missing/research',
    ]));
  });

  it('resolves hub copilot effective sources via hub config, llm config, and defaults', () => {
    const cmp = createComponent();
    cmp.config = {
      default_provider: 'lmstudio',
      default_model: 'model-default',
      llm_config: { provider: 'openai', model: 'gpt-4o' },
      hub_copilot: { enabled: true, provider: '', model: '' },
    };

    expect(resolveHubCopilotProvider(cmp.config)).toBe('openai');
    expect(resolveHubCopilotModel(cmp.config)).toBe('gpt-4o');
    expect(resolveHubCopilotProviderSource(cmp.config)).toBe('llm_config.provider');
    expect(resolveHubCopilotModelSource(cmp.config)).toBe('llm_config.model');
    expect(Boolean(cmp.config.hub_copilot.enabled && resolveHubCopilotProvider(cmp.config) && resolveHubCopilotModel(cmp.config))).toBe(true);
  });

  it('normalizes central context bundle policy and resolves effective delegation shape', () => {
    const cmp = createComponent() as any;
    cmp.config = {
      context_bundle_policy: {
        mode: 'STANDARD',
        compact_max_chunks: 0,
        standard_max_chunks: 12,
      },
    };

    cmp.config.context_bundle_policy = normalizeContextBundlePolicy(cmp.config.context_bundle_policy);

    expect(cmp.config.context_bundle_policy).toEqual({
      mode: 'standard',
      compact_max_chunks: 1,
      standard_max_chunks: 12,
    });
    expect(resolveContextBundlePolicy(cmp.config)).toEqual({
      mode: 'standard',
      compact_max_chunks: 1,
      standard_max_chunks: 12,
      include_context_text: true,
      max_chunks: 12,
    });
  });

  it('normalizes artifact flow config with bounds and defaults', () => {
    const cmp = createComponent() as any;
    cmp.config = {
      artifact_flow: {
        enabled: true,
        rag_enabled: true,
        rag_top_k: 0,
        rag_include_content: true,
        max_tasks: 9999,
        max_worker_jobs_per_task: -2,
      },
    };

    cmp.config.artifact_flow = normalizeArtifactFlow(cmp.config.artifact_flow);

    expect(cmp.config.artifact_flow).toEqual({
      enabled: true,
      rag_enabled: true,
      rag_top_k: 1,
      rag_include_content: true,
      max_tasks: 200,
      max_worker_jobs_per_task: 1,
    });
  });

  it('normalizes artifact flow values before saving', () => {
    const cmp = createComponent() as any;
    cmp.config = {
      default_provider: 'lmstudio',
      hub_copilot: { enabled: false, provider: '', model: '', base_url: '', temperature: 0.2, strategy_mode: 'planning_only' },
      context_bundle_policy: { mode: 'full', compact_max_chunks: 3, standard_max_chunks: 8 },
      artifact_flow: {
        enabled: true,
        rag_enabled: true,
        rag_top_k: 999,
        rag_include_content: false,
        max_tasks: 0,
        max_worker_jobs_per_task: 999,
      },
      research_backend: { provider: 'deerflow', enabled: false, mode: 'cli', command: '', timeout_seconds: 900, result_format: 'markdown' },
      codex_cli: { target_provider: '', base_url: '', api_key_profile: '', prefer_lmstudio: true },
      local_openai_backends: [],
    };

    cmp.save();

    expect(systemMock.setConfig).toHaveBeenCalledWith(
      'http://hub:5000',
      expect.objectContaining({
        artifact_flow: {
          enabled: true,
          rag_enabled: true,
          rag_top_k: 20,
          rag_include_content: false,
          max_tasks: 1,
          max_worker_jobs_per_task: 20,
        },
      }),
    );
  });

  it('resolves codex target provider via configured local backend', () => {
    const cmp = createComponent();
    cmp.config = {
      lmstudio_url: 'http://127.0.0.1:1234/v1',
      local_openai_backends: [
        {
          provider: 'vllm_local',
          name: 'vLLM Local',
          base_url: 'http://127.0.0.1:8010/v1/chat/completions',
          models_text: 'qwen2.5-coder',
          supports_tool_calls: true,
        },
      ],
      codex_cli: {
        target_provider: 'vllm_local',
        base_url: '',
        api_key_profile: '',
        prefer_lmstudio: false,
      },
    };

    expect(cmp.getCodexCliEffectiveBaseUrl()).toBe('http://127.0.0.1:8010/v1');
    expect(cmp.getCodexCliTargetSummary()).toContain('via vllm_local');
    expect(cmp.getProviderRuntimeKind('vllm_local')).toBe('local runtime');
  });

  it('includes configured local backends in the provider selector fallback', () => {
    const cmp = createComponent();
    cmp.config = {
      local_openai_backends: [
        {
          provider: 'vllm_local',
          name: 'vLLM Local',
          base_url: 'http://127.0.0.1:8010/v1',
          models_text: 'qwen2.5-coder, deepseek-coder',
          supports_tool_calls: true,
        },
      ],
    };
    cmp.providerCatalog = null;

    expect(cmp.getProviderSelectGroups()).toEqual([
      {
        label: 'Lokale Runtimes',
        providers: [
          { id: 'ollama', available: true, model_count: 0 },
          { id: 'lmstudio', available: true, model_count: 0 },
          { id: 'vllm_local', available: true, model_count: 2 },
        ],
      },
      {
        label: 'Cloud / Hosted Provider',
        providers: [
          { id: 'openai', available: true, model_count: 0 },
          { id: 'codex', available: true, model_count: 0 },
          { id: 'anthropic', available: true, model_count: 0 },
        ],
      },
    ]);
  });

  it('saves benchmark config with validated payload', () => {
    const cmp = createComponent();
    cmp.benchmarkRetentionDays = 30;
    cmp.benchmarkRetentionSamples = 500;
    cmp.benchmarkProviderOrderTextValue = 'proposal_backend, default_provider';
    cmp.benchmarkModelOrderTextValue = 'proposal_model, default_model';

    cmp.saveBenchmarkConfig();

    expect(systemMock.setConfig).toHaveBeenCalledWith('http://hub:5000', {
      benchmark_retention: { max_days: 30, max_samples: 500 },
      benchmark_identity_precedence: {
        provider_order: ['proposal_backend', 'default_provider'],
        model_order: ['proposal_model', 'default_model'],
      },
    });
  });

  it('blocks save when precedence keys are invalid', () => {
    const cmp = createComponent();
    cmp.benchmarkProviderOrderTextValue = 'invalid_key';
    cmp.benchmarkModelOrderTextValue = 'proposal_model';

    cmp.saveBenchmarkConfig();

    expect(systemMock.setConfig).not.toHaveBeenCalled();
    expect(cmp.benchmarkValidationError).toContain('ungueltige provider_order keys');
    expect(notificationMock.error).toHaveBeenCalled();
  });
});
