import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { readFile } from 'node:fs/promises';
import { of } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  VOICE_AUDIO_CAPTURE,
  VOICE_BATCH_RECORDING,
  VoiceAudioCapturePort,
  VoiceBatchRecordingPort,
} from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import { VoiceConsoleComponent } from './voice-console.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoiceConsoleComponent', () => {
  const api = {
    getCapabilities: vi.fn(),
    getConfigurationSchema: vi.fn(),
    getConfiguration: vi.fn(),
    saveConfiguration: vi.fn(),
    createStream: vi.fn(),
    pushStreamChunk: vi.fn(),
    finalizeStream: vi.fn(),
    cancelStream: vi.fn(),
    transcribe: vi.fn(),
    createReview: vi.fn(),
    decideReview: vi.fn(),
  };
  const capture: VoiceAudioCapturePort = {
    supported: true,
    active: false,
    start: vi.fn(),
    stop: vi.fn(),
  };
  const batch: VoiceBatchRecordingPort = {
    supported: true,
    active: false,
    start: vi.fn(),
    stop: vi.fn(),
    cancel: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['streaming', 'generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_models: [
        { id: 'gemma-3-4b-it', backend: 'generative_corrector', available: true, revision: 'r1' },
        { id: 'phi-4-mini', backend: 'generative_corrector', available: true, revision: 'r2' },
      ],
    }));
    api.getConfigurationSchema.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      fields: [
        { key: 'recognition_strategy', type: 'enum', enum: ['single'] },
        { key: 'primary_backend', type: 'enum', enum: ['vosk'] },
        { key: 'generative_corrector_model', type: 'enum', enum: ['gemma-3-4b-it', 'phi-4-mini'] },
      ],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single',
        primary_backend: 'vosk',
        correction_policy: 'generative_rewrite',
        generative_corrector_model: 'gemma-3-4b-it',
        feature_flags: { generative_corrector: true },
      },
      sources: [],
      version: 1,
    }));
    api.saveConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      scope: 'profile', scope_id: 'default', delta: {}, version: 2,
    }));

    TestBed.configureTestingModule({
      imports: [VoiceConsoleComponent],
      providers: [
        provideRouter([]),
        { provide: VoiceApiService, useValue: api },
        { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'http://hub.test' }] } },
        { provide: VOICE_AUDIO_CAPTURE, useValue: capture },
        { provide: VOICE_BATCH_RECORDING, useValue: batch },
      ],
    });
  });

  it('shows live and batch modes with Hub-provided correction models', () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;

    expect(text).toContain('Live-Transkription');
    expect(text).toContain('gemma-3-4b-it');
    expect(text).toContain('phi-4-mini');
    expect(fixture.componentInstance.selectedBackend).toBe('vosk');
    expect(fixture.componentInstance.selectedCorrectorProvider).toBe('embedded');
    expect(fixture.componentInstance.selectedCorrectorModel).toBe('gemma-3-4b-it');
    expect((fixture.nativeElement as HTMLElement).querySelector('a[href="/settings?section=voice"]')).toBeTruthy();
  });

  it('persists the additive Vosk plus generative rewrite profile contract', async () => {
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.selectedCorrectorModel = 'phi-4-mini';

    await component.saveConfiguration();

    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      scope: 'profile',
      scope_id: 'default',
      delta: expect.objectContaining({
        transport_mode: 'streaming',
        primary_backend: 'vosk',
        correction_policy: 'generative_rewrite',
        review_policy: 'always',
        generative_corrector_provider: 'embedded',
        generative_corrector_model: 'phi-4-mini',
        secondary_backends: [],
        feature_flags: { generative_corrector: true, voice_fusion: false },
      }),
    }), expect.stringContaining('voice-ui:console-configuration:profile:'));
  });

  it('merges console fields into the versioned scope delta without deleting foreign settings', async () => {
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'deterministic',
        generative_corrector_model: 'gemma-3-4b-it',
      },
      sources: [{
        scope: 'profile', scope_id: 'default', version: 7,
        delta: {
          routing_strategy: 'adaptive',
          confidence_threshold: .82,
          feature_flags: { personalization: true, adaptive_routing: true },
        },
      }],
      version: 'effective-7',
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.generativeCorrection = true;
    component.selectedCorrectorModel = 'gemma-3-4b-it';

    await component.saveConfiguration();

    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      expected_version: 7,
      delta: expect.objectContaining({
        routing_strategy: 'adaptive',
        confidence_threshold: .82,
        feature_flags: expect.objectContaining({
          personalization: true,
          adaptive_routing: true,
          generative_corrector: true,
          voice_fusion: false,
        }),
      }),
    }), expect.any(String));
    expect(api.getConfiguration).toHaveBeenCalledTimes(2);
  });

  it('disables unavailable ASR backends and stale corrector selections from Hub capabilities', () => {
    api.getConfigurationSchema.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      fields: [
        { key: 'recognition_strategy', type: 'enum', enum: ['single'] },
        { key: 'primary_backend', type: 'enum', enum: ['vosk', 'whisper_cpp'] },
        { key: 'generative_corrector_model', type: 'string' },
      ],
    }));
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['streaming'],
      models: [
        { id: 'vosk-de', backend: 'vosk', available: false, status: 'unavailable', reason_code: 'model_missing' },
        { id: 'whisper-small', engine: 'whisper_cpp', status: 'ready' },
        { id: 'whisper-fast', engine: 'faster_whisper', status: 'ready' },
        { id: 'voxtral-mini', engine: 'voxtral', status: 'unavailable', reason_code: 'model_missing' },
      ],
      model_catalog: [
        { id: 'vosk-de', engine: 'vosk', status: 'unavailable', reason_code: 'manifest_missing' },
        { id: 'whisper-small', engine: 'whisper_cpp', status: 'ready' },
      ],
      correction_models: [{ id: 'phi-4-mini', available: true, role: 'generative_corrector' }],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_model: 'retired-gemma', feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.asrBackends()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'vosk', available: false, reason: 'model_missing' }),
      expect.objectContaining({ id: 'whisper_cpp', label: 'whisper.cpp', available: true }),
      expect.objectContaining({ id: 'faster_whisper', label: 'faster-whisper', available: true }),
      expect.objectContaining({ id: 'voxtral', label: 'Voxtral', available: false }),
    ]));
    expect(component.backendReason('vosk')).toBe('model_missing');
    expect(component.validBackendSelection()).toBe(false);
    component.selectedBackend = 'whisper_cpp';
    expect(component.validBackendSelection()).toBe(true);
    component.selectedRecognitionStrategy = 'parallel_compare';
    component.selectedSecondaryBackend = 'vosk';
    expect(component.validBackendSelection()).toBe(false);
    expect(component.correctorModels()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'phi-4-mini', available: true }),
      expect.objectContaining({ id: 'retired-gemma', available: false, reason: 'voice.corrector.not_reported' }),
    ]));
    expect(component.validCorrectionSelection()).toBe(false);
  });

  it('filters provider-aware models and persists inherit without endpoint material', async () => {
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_providers: [
        { id: 'embedded', display_name: 'Embedded', available: true, supports_manual_model: false },
        { id: 'ollama', display_name: 'Ollama', available: true, supports_manual_model: true },
        { id: 'lmstudio', display_name: 'LM Studio', available: true, supports_manual_model: true },
      ],
      correction_default: {
        provider: 'ollama', model: 'llama3.2:latest', source: 'default_provider/default_model', available: true,
      },
      correction_models: [
        { id: 'qwen-local', provider: 'embedded', available: true, role: 'generative_corrector' },
        { id: 'shared-model', provider: 'ollama', available: true, role: 'generative_corrector' },
        { id: 'llama3.2:latest', provider: 'ollama', available: true, role: 'generative_corrector' },
        { id: 'shared-model', provider: 'lmstudio', available: true, role: 'generative_corrector' },
      ],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_provider: 'ollama', generative_corrector_model: 'shared-model',
        feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.selectedCorrectorProvider).toBe('ollama');
    expect(component.correctorModels('ollama').map((choice) => choice.id)).toEqual([
      'shared-model', 'llama3.2:latest',
    ]);
    expect(component.correctorModels('lmstudio').map((choice) => choice.id)).toEqual(['shared-model']);
    expect(component.correctorProviders()).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'inherit', label: 'Allgemeine LLM-Vorgabe', available: true }),
      expect.objectContaining({ id: 'ollama', available: true }),
      expect.objectContaining({ id: 'lmstudio', available: true }),
    ]));
    const deploymentNotice = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(deploymentNotice).toContain('übernimmt nur Provider und Modell');
    expect(deploymentNotice).toContain('LMSTUDIO_URL');
    expect(deploymentNotice).toContain('LMSTUDIO_API_KEY');
    expect(deploymentNotice).toContain('OLLAMA_URL');
    expect(deploymentNotice).toContain('OLLAMA_API_KEY');
    expect(deploymentNotice).toContain('Neuerstellung des Corrector-Workers');
    expect(deploymentNotice).toContain('ein bloßer Neustart lädt sie nicht neu');
    expect(deploymentNotice).toContain('docs/voice-quickstart.md');
    expect((fixture.nativeElement as HTMLElement).querySelector('a[href="/settings?section=llm"]')).toBeTruthy();

    component.onCorrectorProviderChange('inherit');
    expect(component.selectedCorrectorModel).toBe('');
    expect(component.validCorrectionSelection()).toBe(true);
    await component.saveConfiguration();

    const mutation = api.saveConfiguration.mock.calls.at(-1)?.[1];
    expect(mutation.delta).toEqual(expect.objectContaining({
      generative_corrector_provider: 'inherit',
      generative_corrector_model: '',
    }));
    expect(mutation.delta).not.toHaveProperty('base_url');
    expect(mutation.delta).not.toHaveProperty('api_key');
  });

  it('accepts a manual model ID for a configured provider even when discovery is offline', async () => {
    api.getCapabilities.mockReturnValue(of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['generative_rewrite'],
      models: [{ id: 'vosk-de', backend: 'vosk', available: true }],
      correction_providers: [
        { id: 'embedded', display_name: 'Embedded', available: true, supports_manual_model: false },
        { id: 'vllm_local', display_name: 'vLLM Local', available: false, supports_manual_model: true },
      ],
      correction_models: [{
        id: 'qwen-local', provider: 'embedded', available: true, role: 'generative_corrector',
      }],
    }));
    api.getConfiguration.mockReturnValue(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        recognition_strategy: 'single', primary_backend: 'vosk', correction_policy: 'generative_rewrite',
        generative_corrector_provider: 'vllm_local', generative_corrector_model: 'Qwen/Qwen2.5-7B-Instruct',
        feature_flags: { generative_corrector: true },
      },
      sources: [], version: 1,
    }));
    const fixture = TestBed.createComponent(VoiceConsoleComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;

    expect(component.manualCorrectorModel).toBe(true);
    expect(component.manualCorrectorModelId).toBe('Qwen/Qwen2.5-7B-Instruct');
    expect(component.validCorrectionSelection()).toBe(true);
    await component.saveConfiguration();
    expect(api.saveConfiguration).toHaveBeenCalledWith('http://hub.test', expect.objectContaining({
      delta: expect.objectContaining({
        generative_corrector_provider: 'vllm_local',
        generative_corrector_model: 'Qwen/Qwen2.5-7B-Instruct',
      }),
    }), expect.any(String));

    vi.clearAllMocks();
    component.manualCorrectorModelId = 'not valid model';
    expect(component.validCorrectionSelection()).toBe(false);
    component.manualCorrectorModelId = 'm'.repeat(182);
    expect(component.validCorrectionSelection()).toBe(false);
    await component.saveConfiguration();
    expect(api.saveConfiguration).not.toHaveBeenCalled();

    component.onCorrectorProviderChange('embedded');
    component.setManualCorrectorModel(true);
    expect(component.correctorProviderSupportsManual()).toBe(false);
    expect(component.validCorrectionSelection()).toBe(false);
  });
});
