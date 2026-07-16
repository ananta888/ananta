import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterRuntimeManagementComponent } from './adapter-runtime-management.component';

describe('AdapterRuntimeManagementComponent', () => {
  const selectedAdapter = signal({
    id: 'adapter-1', name: 'Adapter', version: 2, registry_version: 7,
    base_model_id: 'model-1', status: 'approved',
  });
  const facade = {
    selectedAdapter,
    unloadRuntimeAdapter: vi.fn(() => of({
      adapter_id: 'adapter-1', status: 'succeeded', reason_code: 'adapter_cache_unloaded',
    })),
    rollbackRuntimeAdapter: vi.fn(() => of({
      adapter_id: 'adapter-1', version: 2, status: 'deprecated',
      rollback_target: { type: 'base_model_only' as const, base_model: 'model-1' },
      cache_unload: { adapter_id: 'adapter-1', status: 'succeeded', reason_code: 'adapter_cache_unloaded' },
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [AdapterRuntimeManagementComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('sends the exact confirmed unload contract and labels it as cache-only', () => {
    const component = TestBed.createComponent(AdapterRuntimeManagementComponent).componentInstance;
    component.action = 'unload';
    component.reason = 'Operator frees GPU cache';
    component.confirmed = true;

    component.execute();

    expect(facade.unloadRuntimeAdapter).toHaveBeenCalledWith('adapter-1', {
      confirmed: true, reason: 'Operator frees GPU cache',
    });
    expect(facade.rollbackRuntimeAdapter).not.toHaveBeenCalled();
    expect(component.resultMessage()).toContain('Registry-Freigabe wurde nicht verändert');
  });

  it('keeps runtime rollback separate and exposes retryable service errors', () => {
    facade.rollbackRuntimeAdapter.mockReturnValueOnce(throwError(() => ({ status: 503 })) as any);
    const component = TestBed.createComponent(AdapterRuntimeManagementComponent).componentInstance;
    component.action = 'rollback';
    component.reason = 'Regression requires safe runtime fallback';
    component.confirmed = true;

    component.execute();

    expect(facade.rollbackRuntimeAdapter).toHaveBeenCalledWith('adapter-1', {
      confirmed: true, reason: 'Regression requires safe runtime fallback', expected_version: 7,
    });
    expect(facade.unloadRuntimeAdapter).not.toHaveBeenCalled();
    expect(component.error()).toContain('vorübergehend nicht verfügbar');
  });
});
