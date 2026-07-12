import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';

import { SettingsVoiceComponent } from './settings-voice.component';
import { VoiceApiService } from '../features/voice/voice-api.service';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(
    path.resolve(process.cwd(), 'src/app/features/voice', path.basename(String(resource))),
    'utf8',
  ));
});

describe('SettingsVoiceComponent', () => {
  const api = {
    getConfigurationSchema: vi.fn(() => of({
      schema_version: 'ananta.voice-configuration.v1', type: 'object', properties: {},
    })),
    getConfiguration: vi.fn(() => of({
      schema_version: 'ananta.voice-configuration.v1', effective: {}, sources: [], version: 1,
    })),
    getCapabilities: vi.fn(() => of({
      available: true, provider: 'voice-runtime', capabilities: [],
      models: [{
        id: 'whisper-small', backend: 'whisper_cpp', revision: 'sha256:abc', local: true,
        device: 'cpu', available: true, status: 'ready',
      }],
      limits: { max_audio_mb: 25 },
      privacy: { effective_audio_retention: 'none' },
      resources: { name: 'CPU', status: 'ready', free_bytes: 1024 },
      routing_details: {
        reasons: [{ code: 'voice.routing.local_preferred', message: 'Local policy' }],
        skipped_backends: [{ backend: 'remote-asr', reason_code: 'policy_blocked' }],
      },
    })),
    getConsent: vi.fn(() => of({
      id: null, profile_id: 'default', granted: false, categories: [], retention_days: null, version: 0,
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [SettingsVoiceComponent],
      providers: [provideRouter([]), { provide: VoiceApiService, useValue: api }],
    });
  });

  it('is a visible settings surface and keeps native Voxtral clearly separate', () => {
    const fixture = TestBed.createComponent(SettingsVoiceComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('[data-testid="voice-settings-boundary"]')).toBeTruthy();
    expect(element.querySelector('[data-testid="voice-configuration"]')).toBeTruthy();
    expect(element.querySelector('[data-testid="voice-candidate-review"]')).toBeTruthy();
    expect(element.querySelector('[data-testid="voice-personalization"]')).toBeTruthy();
    expect(element.querySelector('[data-testid="voice-runtime-status"]')).toBeTruthy();
    expect(element.textContent).toContain('Mobile-Local-Sonderpfad');
    expect(element.textContent).toContain('whisper_cpp / whisper-small');
    expect(element.textContent).toContain('voice.routing.local_preferred');
    expect(element.textContent).toContain('policy_blocked');
    expect(element.textContent).toContain('CPU · free_bytes');
    expect(element.querySelector('a')?.getAttribute('href')).toBe('/voxtral-offline');
  });
});
