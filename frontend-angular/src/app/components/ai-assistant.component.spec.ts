import { AiAssistantComponent } from './ai-assistant.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';
import { AiAssistantStorageService } from './ai-assistant-storage.service';

describe('AiAssistantComponent', () => {
  const store: Record<string, string> = {};
  const localStorageMock = {
    getItem: vi.fn((k: string) => (k in store ? store[k] : null)),
    setItem: vi.fn((k: string, v: string) => { store[k] = String(v); }),
    removeItem: vi.fn((k: string) => { delete store[k]; }),
    clear: vi.fn(() => {
      for (const k of Object.keys(store)) delete store[k];
    }),
  };

  function createComponent(): AiAssistantComponent & { [key: string]: any } {
    const cmp = Object.create(AiAssistantComponent.prototype) as AiAssistantComponent & { [key: string]: any };
    cmp['domain'] = new AiAssistantDomainService();
    cmp['storage'] = new AiAssistantStorageService();
    cmp['router'] = { url: '/pair-dev', navigate: vi.fn().mockResolvedValue(true) };
    cmp['dockStateStorageKey'] = 'ananta.ai-assistant.minimized.v1';
    cmp['dockHiddenStorageKey'] = 'ananta.ai-assistant.hidden.v1';
    cmp['historyStorageKey'] = 'ananta.ai-assistant.history.v1';
    cmp.snakeChatPanelTab = 'login';
    cmp.snakeChatPanelOpen = false;
    cmp.pairDevMounted = false;
    cmp.configPanelOpen = false;
    cmp.sharePanelOpen = false;
    cmp.runtimeContext = {
      route: '/settings',
      selectedAgentName: 'hub',
      userRole: 'admin',
      userName: 'admin',
      agents: [{ name: 'hub', role: 'hub', url: 'http://localhost:5000' }],
      teamsCount: 2,
      templatesCount: 3,
      templatesSummary: [
        { name: 'Scrum - Product Owner', description: 'Template fuer Product Backlog und Priorisierung.' },
        { name: 'Scrum - Scrum Master', description: 'Template fuer Moderation und Prozess-Coaching.' },
      ],
      settingsSummary: { llm: { default_provider: 'lmstudio', default_model: 'qwen2.5-coder' } },
      editableSettings: [
        { key: 'default_provider', path: 'config.default_provider', type: 'enum', endpoint: 'POST /config' },
        { key: 'codex_cli', path: 'config.codex_cli', type: 'object', endpoint: 'POST /config' },
        { key: 'http_timeout', path: 'config.http_timeout', type: 'integer', endpoint: 'POST /config' },
      ],
      automationSummary: { autopilot: { running: false } },
      hasConfig: true,
      configSnapshot: {
        default_provider: 'lmstudio',
        sgpt_execution_backend: 'codex',
        codex_cli: { base_url: 'http://127.0.0.1:1234/v1', prefer_lmstudio: true },
      },
    };
    cmp.chatHistory = [];
    cmp.cliRuntime = {
      codex: {
        binary_available: true,
        health_score: 100,
        target_base_url: 'http://127.0.0.1:1234/v1',
        target_is_local: true,
      },
    };
    return cmp;
  }

  beforeEach(() => {
    (globalThis as any).localStorage = localStorageMock;
    localStorageMock.clear();
  });

  it('persists and restores dock minimized state', () => {
    const cmp = createComponent();
    expect(cmp.minimized).toBeUndefined();

    cmp['restoreDockState']();
    expect(cmp.minimized).toBe(true);

    cmp.toggleMinimize();
    expect(cmp.minimized).toBe(false);

    const restored = createComponent();
    restored['restoreDockState']();
    expect(restored.minimized).toBe(false);
  });

  it('opens Pair Dev inside the assistant and expands the stable dock', () => {
    const cmp = createComponent();
    cmp.hidden = true;
    cmp.minimized = true;
    cmp.snakeChatPanelOpen = false;
    cmp.configPanelOpen = true;
    cmp.sharePanelOpen = true;

    cmp.openPairDev();

    expect(cmp.snakeChatPanelTab).toBe('pair');
    expect(cmp.pairDevMounted).toBe(true);
    expect(cmp.snakeChatPanelOpen).toBe(true);
    expect(cmp.hidden).toBe(false);
    expect(cmp.minimized).toBe(false);
    expect(cmp.configPanelOpen).toBe(false);
    expect(cmp.sharePanelOpen).toBe(false);
    expect(store['ananta.ai-snake.panel-open.v1']).toBe('true');
    expect(store['ananta.ai-snake.panel-tab.v1']).toBe(JSON.stringify('pair'));
  });

  it('uses the guarded Pair Dev route before mounting the embedded runtime', () => {
    const cmp = createComponent();
    cmp['router'].url = '/workspace';

    cmp.openPairDev();

    expect(cmp['router'].navigate).toHaveBeenCalledWith(['/pair-dev']);
    expect(cmp.pairDevMounted).toBe(false);
    expect(cmp.snakeChatPanelTab).not.toBe('pair');
  });

  it.each(['mode', 'deprecated'])('migrates a persisted legacy %s tab to chat', legacyTab => {
    store['ananta.ai-snake.panel-tab.v1'] = JSON.stringify(legacyTab);
    const cmp = createComponent();

    cmp['restoreDockState']();

    expect(cmp.snakeChatPanelTab).toBe('chat');
    expect(store['ananta.ai-snake.panel-tab.v1']).toBe(JSON.stringify('chat'));
  });

  it('restores the embedded Pair tab', () => {
    store['ananta.ai-snake.panel-tab.v1'] = JSON.stringify('pair');
    const cmp = createComponent();

    cmp['restoreDockState']();

    expect(cmp.snakeChatPanelTab).toBe('pair');
    expect(cmp.pairDevMounted).toBe(false);

    cmp['openEmbeddedPairDev']();
    expect(cmp.pairDevMounted).toBe(true);
  });

  it('migrates a persisted Pair tab outside the guarded Pair route', () => {
    store['ananta.ai-snake.panel-tab.v1'] = JSON.stringify('pair');
    const cmp = createComponent();
    cmp['router'].url = '/workspace';

    cmp['restoreDockState']();

    expect(cmp.snakeChatPanelTab).toBe('chat');
    expect(cmp.pairDevMounted).toBe(false);
    expect(store['ananta.ai-snake.panel-tab.v1']).toBe(JSON.stringify('chat'));
  });

  it('keeps Pair selected while the assistant is minimized or hidden', () => {
    const cmp = createComponent();
    cmp.hidden = false;
    cmp.minimized = false;
    cmp.openPairDev();

    cmp.toggleMinimize();
    cmp.hideDock();

    expect(cmp.snakeChatPanelTab).toBe('pair');
    expect(cmp.pairDevMounted).toBe(true);
    expect(cmp.snakeChatPanelOpen).toBe(true);
    expect(cmp.minimized).toBe(true);
    expect(cmp.hidden).toBe(true);
  });

  it('keeps the Pair runtime mounted when another assistant tab is shown', () => {
    const cmp = createComponent();
    cmp.openPairDev();

    cmp.openSnakeChatPanelTab('chat');

    expect(cmp.pairDevMounted).toBe(true);
    expect(cmp.snakeChatPanelTab).toBe('chat');
    expect(cmp.snakeChatPanelOpen).toBe(true);
  });

  it('does not mount Hub UI-sync while the Public Pair or media tab is visible', () => {
    const cmp = createComponent();
    cmp['pairOnlyValue'] = false;
    cmp.snakeChatPanelOpen = true;

    cmp.snakeChatPanelTab = 'pair';
    expect(cmp.snakeChatPanelVisible).toBe(false);

    cmp.snakeChatPanelTab = 'media';
    expect(cmp.snakeChatPanelVisible).toBe(false);

    cmp.snakeChatPanelTab = 'chat';
    expect(cmp.snakeChatPanelVisible).toBe(true);
  });

  it('returns to Pair and closes Hub overlays when validated Hub identity is lost', () => {
    const cmp = createComponent();
    cmp.pairDevMounted = true;
    cmp.snakeChatPanelOpen = true;
    cmp.snakeChatPanelTab = 'settings';
    cmp.configPanelOpen = true;
    cmp.sharePanelOpen = true;

    cmp.pairOnly = true;

    expect(cmp.snakeChatPanelTab).toBe('pair');
    expect(cmp.snakeChatPanelOpen).toBe(true);
    expect(cmp.configPanelOpen).toBe(false);
    expect(cmp.sharePanelOpen).toBe(false);
    expect(cmp.snakeChatPanelVisible).toBe(false);
  });

  it('switches to media controls without replacing the mounted Pair runtime', () => {
    const cmp = createComponent();
    cmp.openPairDev();

    cmp.openPairDev('media');

    expect(cmp.pairDevMounted).toBe(true);
    expect(cmp.snakeChatPanelTab).toBe('media');
    expect(cmp.snakeChatPanelOpen).toBe(true);
    expect(store['ananta.ai-snake.panel-tab.v1']).toBe(JSON.stringify('media'));
  });

  it('uses the historic Share footer action to return to the compact Pair surface', () => {
    const cmp = createComponent();
    cmp.openPairDev('media');

    cmp.toggleSharePanel();

    expect(cmp.pairDevMounted).toBe(true);
    expect(cmp.snakeChatPanelTab).toBe('pair');
    expect(cmp.snakeChatPanelOpen).toBe(true);
    expect(cmp.sharePanelOpen).toBe(false);
  });

  it('opens Pair only on route entry and does not reopen it for query changes', () => {
    const cmp = createComponent();
    cmp['pairRouteActive'] = false;
    const open = vi.fn(() => {
      cmp.pairDevMounted = true;
    });
    cmp['openEmbeddedPairDev'] = open;

    cmp['handlePairRouteNavigation']('/pair-dev');
    cmp.minimized = true;
    cmp.hidden = true;
    cmp['handlePairRouteNavigation']('/pair-dev?source=header');

    expect(open).toHaveBeenCalledOnce();
    expect(cmp.minimized).toBe(true);
    expect(cmp.hidden).toBe(true);
  });

  it('restores every supported panel tab, including process', () => {
    store['ananta.ai-snake.panel-tab.v1'] = JSON.stringify('process');
    const cmp = createComponent();

    cmp['restoreDockState']();

    expect(cmp.snakeChatPanelTab).toBe('process');
  });

  it('builds assistant request context from runtime context', () => {
    const cmp = createComponent();
    const ctx = cmp['buildAssistantRequestContext']();
    expect(ctx.route).toBe('/settings');
    expect(ctx.selected_agent).toBe('hub');
    expect(ctx.user.role).toBe('admin');
    expect(ctx.teams_count).toBe(2);
    expect(ctx.templates_count).toBe(3);
    expect(ctx.templates_summary).toEqual([
      { name: 'Scrum - Product Owner', description: 'Template fuer Product Backlog und Priorisierung.' },
      { name: 'Scrum - Scrum Master', description: 'Template fuer Moderation und Prozess-Coaching.' },
    ]);
    expect(ctx.settings_summary?.llm?.default_provider).toBe('lmstudio');
    expect(ctx.editable_settings).toEqual([
      { key: 'default_provider', path: 'config.default_provider', type: 'enum', endpoint: 'POST /config' },
      { key: 'codex_cli', path: 'config.codex_cli', type: 'object', endpoint: 'POST /config' },
      { key: 'http_timeout', path: 'config.http_timeout', type: 'integer', endpoint: 'POST /config' },
    ]);
    expect(ctx.automation_summary?.autopilot?.running).toBe(false);
    expect(ctx.has_config).toBe(true);
  });

  it('persists and restores chat history', () => {
    const cmp = createComponent();
    cmp.chatHistory = [
      { role: 'assistant', content: 'hello' },
      { role: 'user', content: 'change timeout' },
    ] as any;
    cmp['persistChatHistory']();

    const restored = createComponent();
    restored['restoreChatHistory']();
    expect(restored.chatHistory.length).toBe(2);
    expect(restored.chatHistory[0].content).toBe('hello');
    expect(restored.chatHistory[1].content).toBe('change timeout');
  });

  it('summarizes update_config tool changes', () => {
    const cmp = createComponent();
    const summary = cmp.summarizeToolChanges({
      name: 'update_config',
      args: { key: 'http_timeout', value: 33 },
    });
    expect(summary).toContain('config.http_timeout');
    expect(summary).toContain('33');
  });

  it('resolves selected CLI runtime from snapshot config', () => {
    const cmp = createComponent();
    cmp.cliBackend = 'auto';
    const runtime = cmp.selectedCliRuntime();
    expect(runtime?.binary_available).toBe(true);
    expect(runtime?.target_is_local).toBe(true);
  });
});
