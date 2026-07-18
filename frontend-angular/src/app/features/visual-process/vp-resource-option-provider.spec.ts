import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { NEVER, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { VP_RESOURCE_OPTION_TTL_MS, VpResourceOptionProvider } from './vp-resource-option-provider';

describe('VpResourceOptionProvider', () => {
  let provider: VpResourceOptionProvider;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: [
      VpResourceOptionProvider,
      { provide: VP_RESOURCE_OPTION_TTL_MS, useValue: 1_000 },
    ] });
    provider = TestBed.inject(VpResourceOptionProvider);
  });

  afterEach(() => vi.useRealTimers());

  it('reports loading, ready and empty and projects only safe authorized option fields', () => {
    provider.setStatic('models', [{ id: 'foreign', label: 'Foreign', tenant_id: 'tenant-b' }, {
      id: 'model-1', label: 'Model 1', tenant_id: 'tenant-a', description: 'Local', api_key: 'must-not-leak',
    }], { tenantId: 'tenant-a' });
    expect(provider.snapshot('models')).toMatchObject({ status: 'ready', options: [{ value: 'model-1', label: 'Model 1', description: 'Local' }] });
    expect(JSON.stringify(provider.snapshot('models'))).not.toContain('must-not-leak');
    expect(JSON.stringify(provider.snapshot('models'))).not.toContain('tenant-a');

    provider.setStatic('empty', []);
    expect(provider.snapshot('empty').status).toBe('empty');
  });

  it('honors TTL, refresh and explicit cache invalidation', () => {
    const loader = vi.fn(() => of([{ id: 'skill-1', label: 'Skill' }]));
    provider.register('skills', loader);
    provider.load('skills');
    expect(loader).toHaveBeenCalledTimes(1);
    provider.load('skills');
    expect(loader).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1_001);
    provider.load('skills');
    expect(loader).toHaveBeenCalledTimes(2);
    provider.refresh('skills');
    expect(loader).toHaveBeenCalledTimes(3);
    provider.invalidate('skills');
    expect(provider.snapshot('skills')).toMatchObject({ status: 'degraded', reason: 'cache_invalidated' });
  });

  it('distinguishes 403, preserves stale values on 404 and detects a timeout', async () => {
    provider.register('forbidden', () => throwError(() => new HttpErrorResponse({ status: 403 })));
    provider.load('forbidden');
    expect(provider.snapshot('forbidden')).toMatchObject({ status: 'failed', reason: 'forbidden', options: [] });

    let deleted = false;
    provider.register('deleted', () => deleted
      ? throwError(() => new HttpErrorResponse({ status: 404 }))
      : of([{ id: 'dataset-1', label: 'Dataset' }]));
    provider.load('deleted'); deleted = true; provider.refresh('deleted');
    expect(provider.snapshot('deleted')).toMatchObject({ status: 'degraded', reason: 'not_found' });
    expect(provider.snapshot('deleted').options[0]).toMatchObject({ value: 'dataset-1', stale: true });

    provider.register('slow', () => NEVER, { timeoutMs: 50 });
    provider.load('slow');
    expect(provider.snapshot('slow').status).toBe('loading');
    await vi.advanceTimersByTimeAsync(51);
    expect(provider.snapshot('slow')).toMatchObject({ status: 'failed', reason: 'timeout' });
  });

  it('keeps removed options visible but disabled in an explained degraded state', () => {
    provider.setStatic('processes', [{ id: 'process-1', label: 'Process 1' }, { id: 'process-2', label: 'Process 2' }]);
    provider.setStatic('processes', [{ id: 'process-2', label: 'Process 2' }]);
    expect(provider.snapshot('processes')).toMatchObject({ status: 'degraded', reason: 'resource_removed' });
    expect(provider.snapshot('processes').options.find(option => option.value === 'process-1')).toMatchObject({ disabled: true, stale: true });
  });
});
