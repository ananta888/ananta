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
    expect(fixture.componentInstance.selectedCorrectorModel).toBe('gemma-3-4b-it');
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
      expect.objectContaining({ id: 'whisper_cpp', available: true }),
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
});
