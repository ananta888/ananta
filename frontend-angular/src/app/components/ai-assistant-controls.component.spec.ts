import { AiAssistantControlsComponent } from './ai-assistant-controls.component';

describe('AiAssistantControlsComponent', () => {
  function createComponent(): AiAssistantControlsComponent {
    const cmp = new AiAssistantControlsComponent();
    cmp.availableCliBackends = ['auto', 'codex', 'sgpt'];
    return cmp;
  }

  it('maps cli backend ids to operator-friendly labels', () => {
    const cmp = createComponent();

    expect(cmp.backendLabel('auto')).toBe('Auto');
    expect(cmp.backendLabel('codex')).toBe('Codex CLI');
    expect(cmp.backendLabel('sgpt')).toBe('ShellGPT');
    expect(cmp.backendLabel('opencode')).toBe('OpenCode');
    expect(cmp.backendLabel('aider')).toBe('Aider');
    expect(cmp.backendLabel('mistral_code')).toBe('Mistral Code');
  });

  it('exposes a stable default runtime context', () => {
    const cmp = createComponent();

    expect(cmp.runtimeContext.route).toBe('/');
    expect(cmp.runtimeContext.agents).toEqual([]);
    expect(cmp.runtimeContext.hasConfig).toBe(false);
  });
});
