import { of } from 'rxjs';

import { CliBackendSetupComponent } from './cli-backend-setup.component';

describe('CliBackendSetupComponent', () => {
  function componentWithWorker(): any {
    const component = Object.create(CliBackendSetupComponent.prototype) as any;
    component.workers = [{
      name: 'ananta-worker-1',
      url: 'http://ai-agent-alpha:5000',
      status: 'online',
    }];
    component.provisioningState = {
      'codex|http://ai-agent-alpha:5000': 'ready',
    };
    component.provisioningDetails = {
      'codex|http://ai-agent-alpha:5000': {
        binary_path: '/data/codex',
      },
    };
    component.codexWorkerName = 'ananta-worker-1';
    component.claudeWorkerName = 'ananta-worker-1';
    component.codexPermissionMode = 'workspace-write';
    component.claudeEnabled = true;
    component.claudeAuthMode = 'claude_login';
    component.claudePermissionMode = 'plan';
    component.claudeDefaultModel = '';
    component.claudeTimeoutSeconds = 120;
    component.system = {
      resolveHubAgent: () => ({ name: 'hub', url: 'https://hub.example' }),
    };
    component.agentApi = {
      setConfig: vi.fn(() => of({})),
      sgptBackendWorkerAction: vi.fn(() => of({
        worker: { name: 'ananta-worker-1' },
        status: 'ready',
        binary_available: true,
        binary_path: '/data/codex',
      })),
    };
    component.ns = { error: vi.fn() };
    component.cdr = { detectChanges: vi.fn() };
    return component;
  }

  it('derives card status from the selected Worker installation', () => {
    const component = componentWithWorker();

    expect(component.executionStatus('codex')).toBe('ready');
  });

  it('routes diagnostics to the selected execution Worker', () => {
    const component = componentWithWorker();
    const card = {
      diagnosing: false,
      diagnose: null,
      testResult: null,
    };

    component.diagnose('codex', card);

    expect(component.agentApi.sgptBackendWorkerAction).toHaveBeenCalledWith(
      'https://hub.example',
      'codex',
      { worker_name: 'ananta-worker-1', action: 'diagnose' },
    );
    expect(card.diagnose.worker.name).toBe('ananta-worker-1');
  });

  it('persists current Claude opt-in before routing its test run', () => {
    const component = componentWithWorker();
    const card = {
      testRunning: false,
      testResult: null,
    };

    component.testRun('claude_code', card);

    expect(component.agentApi.setConfig).toHaveBeenCalledWith(
      'https://hub.example',
      {
        claude_cli: {
          enabled: true,
          auth_mode: 'claude_login',
          permission_mode: 'plan',
          timeout_seconds: 120,
          default_model: null,
        },
      },
    );
    expect(component.agentApi.sgptBackendWorkerAction).toHaveBeenCalledWith(
      'https://hub.example',
      'claude_code',
      {
        worker_name: 'ananta-worker-1',
        action: 'test_run',
        prompt: 'Antworte nur mit dem Wort: OK',
        timeout: 120,
      },
    );
  });

  it('persists the selected Codex sandbox before its test run', () => {
    const component = componentWithWorker();
    component.codexAuthMode = 'chatgpt_login';
    component.codexApiKeyProfile = '';
    const card = {
      testRunning: false,
      testResult: null,
    };

    component.testRun('codex', card);

    expect(component.agentApi.setConfig).toHaveBeenCalledWith(
      'https://hub.example',
      {
        codex_cli: {
          auth_mode: 'chatgpt_login',
          api_key_profile: null,
          sandbox_mode: 'workspace-write',
        },
      },
    );
  });
});
