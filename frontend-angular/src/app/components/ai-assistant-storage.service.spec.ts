import { AiAssistantStorageService } from './ai-assistant-storage.service';

describe('AiAssistantStorageService', () => {
  const store: Record<string, string> = {};
  const localStorageMock = {
    getItem: vi.fn((k: string) => (k in store ? store[k] : null)),
    setItem: vi.fn((k: string, v: string) => { store[k] = String(v); }),
    removeItem: vi.fn((k: string) => { delete store[k]; }),
    clear: vi.fn(() => {
      for (const k of Object.keys(store)) delete store[k];
    }),
  };

  beforeEach(() => {
    (globalThis as any).localStorage = localStorageMock;
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it('persists and restores pending plans', () => {
    const service = new AiAssistantStorageService();

    service.persistPendingPlan('assistant.plan', {
      role: 'assistant',
      content: 'review changes',
      pendingPrompt: 'run the plan',
      toolCalls: [{ name: 'exec_command', args: { cmd: 'git status' } }],
    } as any);

    expect(service.restorePendingPlan('assistant.plan')).toEqual({
      pendingPrompt: 'run the plan',
      toolCalls: [{ name: 'exec_command', args: { cmd: 'git status' } }],
    });
  });

  it('ignores invalid persisted plans', () => {
    const service = new AiAssistantStorageService();
    localStorageMock.setItem('assistant.plan', JSON.stringify({ pendingPrompt: '', toolCalls: [] }));

    expect(service.restorePendingPlan('assistant.plan')).toBeNull();
  });

  it('clears persisted state', () => {
    const service = new AiAssistantStorageService();
    localStorageMock.setItem('assistant.plan', JSON.stringify({ pendingPrompt: 'x', toolCalls: [{}] }));

    service.clear('assistant.plan');

    expect(localStorageMock.getItem('assistant.plan')).toBeNull();
  });
});
