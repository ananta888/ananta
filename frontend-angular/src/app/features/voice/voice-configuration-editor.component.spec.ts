import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { of } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceConfigurationEditorComponent } from './voice-configuration-editor.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoiceConfigurationEditorComponent', () => {
  const commonMetadata = {
    scopes: ['global', 'profile', 'session'],
    visibility: 'standard',
    secret_reference: false,
  };
  const hubSchema = {
    schema_version: 'ananta.voice-configuration.v1',
    type: 'object',
    additionalProperties: false,
    properties: {
      transport_mode: {
        type: 'string', enum: ['batch', 'streaming'], default: 'batch', ...commonMetadata,
      },
      recognition_strategy: {
        type: 'string', enum: ['single', 'parallel_compare'], default: 'single', ...commonMetadata,
      },
      primary_backend: {
        type: 'string', enum: ['vosk', 'whisper_cpp'], default: 'vosk',
        capability_reason_source: '/v1/voice/capabilities/model_catalog', ...commonMetadata,
      },
      secondary_backends: {
        type: 'array', maxItems: 3, uniqueItems: true,
        items: { type: 'string', enum: ['vosk', 'whisper_cpp'] },
        capability_reason_source: '/v1/voice/capabilities/model_catalog', ...commonMetadata,
      },
      enhancement_variants: {
        type: 'array', maxItems: 4, uniqueItems: true, default: ['original'],
        items: { type: 'string', enum: ['original', 'normalized'] },
        required_capabilities: ['voice_fusion', 'audio_enhancement'], ...commonMetadata,
      },
      feature_flags: {
        type: 'object', additionalProperties: false, ...commonMetadata,
        properties: {
          restricted_worker: { type: 'boolean', default: false, ...commonMetadata },
        },
      },
    },
    precedence: ['defaults', 'legacy_global', 'global_delta', 'profile_delta', 'session_delta'],
  };
  const api = {
    getConfigurationSchema: vi.fn(() => of(hubSchema)),
    getConfiguration: vi.fn(() => of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: {
        transport_mode: 'batch', recognition_strategy: 'parallel_compare',
        primary_backend: 'whisper_cpp', secondary_backends: ['vosk'], enhancement_variants: ['original'],
        feature_flags: { restricted_worker: true },
      },
      sources: [{ scope: 'global', version: 2, delta: { recognition_strategy: 'parallel_compare' } }],
      version: 2,
      adjustments: [],
    })),
    getCapabilities: vi.fn(() => of({
      available: true,
      provider: 'voice-runtime',
      capabilities: ['transcription'],
      limits: { max_audio_mb: 25 },
      privacy: { effective_audio_retention: 'none' },
      models: [{
        id: 'whisper-small', backend: 'whisper_cpp', revision: 'sha256:abc', local: true,
        device: 'cpu', available: true, status: 'ready',
      }],
      routing_details: {
        reasons: [{ code: 'voice.routing.local_preferred', message: 'Local policy' }],
        skipped_backends: [{ backend: 'remote-asr', reason_code: 'policy_blocked' }],
      },
    })),
    saveConfiguration: vi.fn((_url, mutation) => of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: mutation.delta,
      sources: [{ scope: mutation.scope, scope_id: mutation.scope_id, version: 3, delta: mutation.delta }],
      version: 3,
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [VoiceConfigurationEditorComponent],
      providers: [{ provide: VoiceApiService, useValue: api }],
    });
  });

  it('renders schema-driven orthogonal controls and capability status', () => {
    const fixture = TestBed.createComponent(VoiceConfigurationEditorComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Transport mode');
    expect(text).toContain('Recognition strategy');
    expect(text).toContain('Secondary backends');
    expect(text).toContain('Restricted worker');
    expect(text).toContain('Scopes: global, profile, session');
    expect(text).toContain('Benötigte Capabilities: voice_fusion, audio_enhancement');
    expect(text).toContain('Capability-Gründe vom Hub: /v1/voice/capabilities/model_catalog');
    expect(api.getConfigurationSchema).toHaveBeenCalledWith('http://hub.test');
  });

  it('preserves sparse scope deltas and resets them through the Hub API', () => {
    const fixture = TestBed.createComponent(VoiceConfigurationEditorComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const recognition = component.fields().find((field) => field.key === 'recognition_strategy')!;

    expect(component.hasOverride(recognition)).toBe(true);
    component.setValue(recognition, 'single');
    component.save();
    expect(api.saveConfiguration).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ scope: 'global', delta: { recognition_strategy: 'single' }, expected_version: 2 }),
      expect.stringContaining('voice-ui:configuration:global:'),
    );

    component.resetDelta();
    expect(api.saveConfiguration).toHaveBeenLastCalledWith(
      'http://hub.test',
      expect.objectContaining({ scope: 'global', delta: {} }),
      expect.any(String),
    );
  });

  it('renders and blocks a real Hub effective adjustment with its shared reason code', () => {
    api.getConfiguration.mockReturnValueOnce(of({
      schema_version: 'ananta.voice-configuration.v1',
      effective: { enhancement_variants: ['original'], feature_flags: {} },
      sources: [{
        scope: 'global', version: 3,
        delta: { enhancement_variants: ['original', 'normalized'] },
      }],
      version: 3,
      adjustments: [{
        field: 'enhancement_variants',
        requested: 'original,normalized',
        effective: 'original',
        reason_code: 'audio_enhancement_disabled',
      }],
    }));
    const fixture = TestBed.createComponent(VoiceConfigurationEditorComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const enhancement = component.fields().find((field) => field.key === 'enhancement_variants')!;

    expect(enhancement.scopes).toEqual(['global', 'profile', 'session']);
    expect(enhancement.required_capabilities).toEqual(['voice_fusion', 'audio_enhancement']);
    expect(component.fieldError(enhancement)).toBe('audio_enhancement_disabled');

    const reason = (fixture.nativeElement as HTMLElement).querySelector('[data-reason-code]');
    expect(reason?.getAttribute('data-reason-code')).toBe('audio_enhancement_disabled');
    expect(reason?.textContent).toContain('angefordert: original,normalized');
    expect(reason?.textContent).toContain('wirksam: original');
    expect(component.hasValidationErrors()).toBe(true);

    component.removeOverride(enhancement);
    expect(component.fieldError(enhancement)).toBeNull();
    component.save();
    expect(api.saveConfiguration).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({ scope: 'global', delta: {}, expected_version: 3 }),
      expect.stringContaining('voice-ui:configuration:global:'),
    );
  });

  it('blocks duplicate primary and secondary backends with the Hub reason code', () => {
    const fixture = TestBed.createComponent(VoiceConfigurationEditorComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const resolvedPrimary = component.fields().find((field) => field.key === 'primary_backend')!;
    const secondary = component.fields().find((field) => field.key === 'secondary_backends')!;
    component.setValue(resolvedPrimary, 'vosk');
    component.setValue(secondary, ['vosk']);

    expect(component.combinationErrors()).toEqual(['voice_configuration.duplicate_backend']);
    component.save();
    expect(api.saveConfiguration).not.toHaveBeenCalled();
  });
});
