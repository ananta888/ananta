import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { of, throwError } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceRuntimeStatusComponent } from './voice-runtime-status.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VoiceRuntimeStatusComponent', () => {
  const api = { getCapabilities: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [VoiceRuntimeStatusComponent],
      providers: [{ provide: VoiceApiService, useValue: api }],
    });
  });

  it('shows local execution, revision, resources and routing reasons from Hub capabilities', () => {
    api.getCapabilities.mockReturnValue(of({
      available: true, provider: 'voice-runtime', capabilities: [],
      models: [{ id: 'model-a', backend: 'vosk', revision: 'rev-a', local: true, device: 'cpu', status: 'ready' }],
      model_catalog: [{ id: 'model-a', backend: 'vosk', revision: 'rev-a', local: true, device: 'cpu', status: 'ready' }],
      correction_models: [{
        id: 'gemma-2b-it', provider: 'embedded', role: 'generative_corrector', revision: 'rev-g', local: true,
        device: 'cpu', status: 'configured', available: true,
      }, {
        id: 'gemma-2b-it', provider: 'ollama', role: 'generative_corrector', revision: 'latest', local: true,
        device: 'cpu', status: 'configured', available: true,
      }],
      resources: { name: 'RAM', free_bytes: 4096 },
      routing_details: { reasons: [{ code: 'voice.routing.fixed' }] },
    }));
    const fixture = TestBed.createComponent(VoiceRuntimeStatusComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('vosk / model-a');
    expect(text).toContain('embedded / gemma-2b-it');
    expect(text).toContain('ollama / gemma-2b-it');
    expect(text).toContain('rev-a');
    expect(text).toContain('Lokal');
    expect(text).toContain('RAM · free_bytes');
    expect(text).toContain('voice.routing.fixed');
    expect(api.getCapabilities).toHaveBeenCalledWith('http://hub.test');
    expect(fixture.componentInstance.models()).toHaveLength(3);
  });

  it('renders a stable policy error instead of a generic runtime failure', () => {
    api.getCapabilities.mockReturnValue(throwError(() => ({
      error: { data: { error: { code: 'policy_denied', message: 'voice_exposure_disabled' } } },
    })));
    const fixture = TestBed.createComponent(VoiceRuntimeStatusComponent);
    fixture.componentRef.setInput('hubUrl', 'http://hub.test');
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('policy_denied');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('voice_exposure_disabled');
  });
});
