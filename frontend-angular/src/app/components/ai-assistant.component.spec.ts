import { AiAssistantComponent } from './ai-assistant.component';
import { AiAssistantDomainService } from './ai-assistant-domain.service';

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
    cmp['historyStorageKey'] = 'ananta.ai-assistant.history.v1';
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
      hasConfig: true,
      configSnapshot: { default_provider: 'lmstudio' },
    };
    cmp.chatHistory = [];
    return cmp;
  }

  beforeEach(() => {
    (globalThis as any).localStorage = localStorageMock;
    localStorageMock.clear();
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
});
