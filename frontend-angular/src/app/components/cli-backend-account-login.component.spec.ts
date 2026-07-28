import { of } from 'rxjs';

import { CliBackendAccountLoginComponent } from './cli-backend-account-login.component';

describe('CliBackendAccountLoginComponent', () => {
  function createComponent(): any {
    const component = Object.create(CliBackendAccountLoginComponent.prototype) as any;
    component.backendId = 'codex';
    component.workerName = 'ananta-worker-1';
    component.ready = true;
    component.claudeEnabled = false;
    component.claudePermissionMode = 'plan';
    component.claudeTimeoutSeconds = 1800;
    component.state = {
      checking: false,
      authenticated: false,
      running: false,
      sessionId: '',
      status: 'not_authenticated',
      verificationUrl: '',
      userCode: '',
      requiresInput: false,
      input: '',
      error: '',
    };
    component.system = {
      resolveHubAgent: () => ({ name: 'hub', url: 'https://hub.example' }),
    };
    component.agentApi = {
      setConfig: vi.fn(() => of({})),
      sgptBackendWorkerAction: vi.fn(() => of({
        worker: { name: 'ananta-worker-1' },
        session_id: 'opaque-session',
        status: 'pending',
        verification_url: 'https://auth.openai.com/codex/device',
        user_code: 'ABCD-EFGHI',
      })),
    };
    component.notifications = { success: vi.fn(), error: vi.fn() };
    component.cdr = { detectChanges: vi.fn() };
    return component;
  }

  it('starts Codex device login on the selected Worker', () => {
    vi.useFakeTimers();
    const component = createComponent();

    component.start();

    expect(component.agentApi.setConfig).toHaveBeenCalledWith(
      'https://hub.example',
      { codex_cli: { auth_mode: 'chatgpt_login' } },
    );
    expect(component.agentApi.sgptBackendWorkerAction).toHaveBeenCalledWith(
      'https://hub.example',
      'codex',
      { worker_name: 'ananta-worker-1', action: 'login_start' },
    );
    expect(component.state.userCode).toBe('ABCD-EFGHI');
    expect(component.state.verificationUrl).toBe(
      'https://auth.openai.com/codex/device',
    );
    component.ngOnDestroy();
    vi.useRealTimers();
  });

  it('submits the Claude browser return code to the same Worker session', () => {
    const component = createComponent();
    component.backendId = 'claude_code';
    component.state.sessionId = 'opaque-session';
    component.state.input = 'browser-return-code';

    component.submitInput();

    expect(component.agentApi.sgptBackendWorkerAction).toHaveBeenCalledWith(
      'https://hub.example',
      'claude_code',
      {
        worker_name: 'ananta-worker-1',
        action: 'login_input',
        session_id: 'opaque-session',
        value: 'browser-return-code',
      },
    );
    expect(component.state.input).toBe('');
  });
});
