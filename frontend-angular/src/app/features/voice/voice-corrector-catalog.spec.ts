import {
  buildCorrectorModels,
  buildCorrectorProviders,
  correctionDefaultLabel,
  correctorProviderSupportsManual,
  validCorrectorModelId,
} from './voice-corrector-catalog';
import { VoiceCapabilityStatus, VoiceConfiguration } from './voice.models';

const configuration = (effective: Record<string, unknown>): VoiceConfiguration => ({
  schema_version: 'ananta.voice-configuration.v1',
  effective,
  sources: [],
  version: 1,
});

describe('voice corrector catalog', () => {
  it('shows how the general auto target was resolved by the Hub', () => {
    const capabilities: VoiceCapabilityStatus = {
      available: true,
      provider: 'voice-runtime',
      capabilities: [],
      models: [],
      correction_default: {
        provider: 'lmstudio',
        configured_model: 'auto',
        model: 'qwen2.5-3b-instruct',
        source: 'settings.default',
        available: true,
      },
    };

    expect(correctionDefaultLabel(capabilities)).toContain('auto → qwen2.5-3b-instruct');
    const providers = buildCorrectorProviders(
      capabilities,
      configuration({ generative_corrector_provider: 'inherit' }),
    );
    expect(providers.filter((provider) => provider.id === 'inherit')).toHaveLength(1);
    expect(providers[0]).toEqual(expect.objectContaining({ id: 'inherit', available: true }));
  });

  it('maps a legacy model-only configuration to embedded', () => {
    const capabilities: VoiceCapabilityStatus = {
      available: true,
      provider: 'voice-runtime',
      capabilities: [],
      models: [],
      correction_models: [{ id: 'qwen-local', available: true }],
    };
    const current = configuration({ generative_corrector_model: 'qwen-local' });

    expect(buildCorrectorProviders(capabilities, current)).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'embedded', available: true }),
    ]));
    expect(buildCorrectorModels(capabilities, current, [], 'embedded')).toEqual([
      expect.objectContaining({ id: 'qwen-local', available: true }),
    ]);
  });

  it('keeps equal model IDs distinct by selected provider', () => {
    const capabilities: VoiceCapabilityStatus = {
      available: true,
      provider: 'voice-runtime',
      capabilities: [],
      models: [],
      correction_providers: [
        { id: 'ollama', display_name: 'Ollama', available: true, supports_manual_model: true },
        { id: 'lmstudio', display_name: 'LM Studio', available: true, supports_manual_model: false },
      ],
      correction_default: { provider: 'ollama', model: 'shared', source: 'default_model', available: true },
      correction_models: [
        { id: 'shared', provider: 'ollama', available: true },
        { id: 'ollama-only', provider: 'ollama', available: true },
        { id: 'shared', provider: 'lmstudio', available: true },
      ],
    };
    const current = configuration({
      generative_corrector_provider: 'ollama',
      generative_corrector_model: 'shared',
    });

    expect(buildCorrectorModels(capabilities, current, [], 'ollama').map((model) => model.id)).toEqual([
      'shared', 'ollama-only',
    ]);
    expect(buildCorrectorModels(capabilities, current, [], 'lmstudio').map((model) => model.id)).toEqual([
      'shared',
    ]);
    expect(buildCorrectorProviders(capabilities, current)[0]).toEqual(expect.objectContaining({
      id: 'inherit', available: true,
    }));
  });

  it('allows manual model syntax only when the Hub grants it to the provider', () => {
    const capabilities: VoiceCapabilityStatus = {
      available: true,
      provider: 'voice-runtime',
      capabilities: [],
      models: [],
      correction_providers: [{
        id: 'vllm_local', display_name: 'vLLM', available: true, supports_manual_model: true,
      }],
    };

    expect(correctorProviderSupportsManual(capabilities, 'vllm_local')).toBe(true);
    expect(correctorProviderSupportsManual(capabilities, 'embedded')).toBe(false);
    expect(validCorrectorModelId('Qwen/Qwen2.5-7B-Instruct')).toBe(true);
    expect(validCorrectorModelId('not valid model')).toBe(false);
    expect(validCorrectorModelId('m'.repeat(184), 'lmstudio')).toBe(false);
    expect(validCorrectorModelId('m'.repeat(182), 'lmstudio')).toBe(true);
  });
});
