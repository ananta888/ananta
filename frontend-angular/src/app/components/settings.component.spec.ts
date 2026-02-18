import { of, throwError } from 'rxjs';

import { SettingsComponent } from './settings.component';

describe('SettingsComponent (benchmark config)', () => {
  const hubApiMock = {
    getLlmBenchmarksConfig: vi.fn(() => of({})),
    setConfig: vi.fn(() => of({})),
  };
  const notificationMock = {
    success: vi.fn(),
    error: vi.fn(),
  };

  function createComponent(): SettingsComponent {
    const cmp = Object.create(SettingsComponent.prototype) as SettingsComponent & { hubApi: any; ns: any };
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.config = {};
    cmp.providerCatalog = null;
    cmp.benchmarkConfig = null;
    cmp.hubApi = hubApiMock;
    cmp.ns = notificationMock;
    cmp.benchmarkRetentionDays = 90;
    cmp.benchmarkRetentionSamples = 2000;
    cmp.benchmarkProviderOrderTextValue = '';
    cmp.benchmarkModelOrderTextValue = '';
    cmp.benchmarkValidationError = '';
    return cmp;
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
    hubApiMock.getLlmBenchmarksConfig.mockReturnValueOnce(
      of({ retention: { max_days: 30, max_samples: 1000 } })
    );
    cmp.loadBenchmarkConfig();
    expect(cmp.benchmarkConfig.retention.max_days).toBe(30);

    hubApiMock.getLlmBenchmarksConfig.mockReturnValueOnce(
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

  it('saves benchmark config with validated payload', () => {
    const cmp = createComponent();
    cmp.benchmarkRetentionDays = 30;
    cmp.benchmarkRetentionSamples = 500;
    cmp.benchmarkProviderOrderTextValue = 'proposal_backend, default_provider';
    cmp.benchmarkModelOrderTextValue = 'proposal_model, default_model';

    cmp.saveBenchmarkConfig();

    expect(hubApiMock.setConfig).toHaveBeenCalledWith('http://hub:5000', {
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

    expect(hubApiMock.setConfig).not.toHaveBeenCalled();
    expect(cmp.benchmarkValidationError).toContain('ungueltige provider_order keys');
    expect(notificationMock.error).toHaveBeenCalled();
  });
});
