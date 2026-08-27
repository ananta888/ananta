import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CodeCompassSiraStatusComponent } from './codecompass-sira-status.component';
import { CodeCompassSiraApiService } from '../services/codecompass-sira-api.service';


describe('CodeCompassSiraStatusComponent', () => {
  const api = { status: vi.fn() };

  beforeEach(async () => {
    api.status.mockReset();
    await TestBed.configureTestingModule({
      imports: [CodeCompassSiraStatusComponent],
      providers: [{ provide: CodeCompassSiraApiService, useValue: api }],
    }).compileComponents();
  });

  it('renders redacted rollout and index status', () => {
    api.status.mockReturnValue(of({
      data: {
        schema: 'codecompass.sira-status.v1',
        status: 'ready',
        config_status: 'ready',
        config_reason: 'sira_config_valid',
        config: { profile_version: 'corpus-discriminative-lexical.v1' },
        flags: {},
        index: { status: 'ready', reason: 'sira_layers_current', artifact_count: 12 },
        rollout: {
          mode: 'shadow',
          result_affecting: false,
          shadow_non_effecting: true,
          kill_switches: {},
        },
      },
    }));
    const fixture = TestBed.createComponent(CodeCompassSiraStatusComponent);
    fixture.componentRef.setInput('baseUrl', 'http://hub.local');
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(api.status).toHaveBeenCalledWith('http://hub.local');
    expect(text).toContain('shadow');
    expect(text).toContain('12');
    expect(text).toContain('beeinflussen die produktive Auswahl nicht');
  });

  it('keeps an API error visible and retryable', () => {
    api.status.mockReturnValue(throwError(() => new Error('offline')));
    const fixture = TestBed.createComponent(CodeCompassSiraStatusComponent);
    fixture.componentRef.setInput('baseUrl', 'http://hub.local');
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'SIRA-Status konnte nicht geladen werden.',
    );
    expect(fixture.nativeElement.querySelector('button').disabled).toBe(false);
  });
});
