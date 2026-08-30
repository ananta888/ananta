import { TestBed } from '@angular/core/testing';

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
    expect(cmp.backendLabel('qwen_code')).toBe('Qwen Code');
    expect(cmp.backendLabel('gemini_cli')).toBe('Gemini CLI');
    expect(cmp.backendLabel('copilot_cli')).toBe('GitHub Copilot CLI');
    expect(cmp.backendLabel('cline')).toBe('Cline');
    expect(cmp.backendLabel('kilo_code')).toBe('Kilo Code');
  });

  it('shows free class, inference target and unavailable reason separately', () => {
    const cmp = createComponent();
    cmp.cliBackend = 'qwen_code';
    cmp.backendMetadata = {
      qwen_code: {
        free_class: 'open_source_byok',
        available: false,
        install_hint: 'npm install qwen',
      },
    };
    cmp.selectedCliRuntime = { target_provider: 'openrouter' };

    expect(cmp.backendOptionLabel('qwen_code')).toContain('Open Source / BYOK');
    expect(cmp.backendOptionLabel('qwen_code')).toContain('nicht installiert');
    expect(cmp.selectedBackendExplanation()).toContain('Ziel: openrouter');
    expect(cmp.selectedBackendExplanation()).toContain('npm install qwen');
  });

  it('exposes a stable default runtime context', () => {
    const cmp = createComponent();

    expect(cmp.runtimeContext.route).toBe('/');
    expect(cmp.runtimeContext.agents).toEqual([]);
    expect(cmp.runtimeContext.hasConfig).toBe(false);
  });

  it('keeps the supported hybrid context mode operator-selectable', async () => {
    await TestBed.configureTestingModule({
      imports: [AiAssistantControlsComponent],
    }).compileComponents();

    const fixture = TestBed.createComponent(AiAssistantControlsComponent);
    fixture.detectChanges();

    const toggle = fixture.nativeElement.querySelector(
      '[data-testid="assistant-hybrid-toggle"]'
    ) as HTMLInputElement | null;
    expect(toggle).not.toBeNull();
    expect(toggle?.getAttribute('aria-label')).toBe('Hybrid Context');
  });
});
