import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ModelTrainingApiService } from '../model-training/model-training-api.service';
import { VpModelTrainingOptionsService } from './vp-model-training-options.service';

describe('VpModelTrainingOptionsService', () => {
  it('loads normalized datasets and bounded profiles only through the Hub APIs', async () => {
    const directory = { list: vi.fn(() => [{ name: 'hub', role: 'hub', url: 'http://hub.test/' }]) };
    const api = {
      capabilities: vi.fn(() => of({ capabilities: {
        available: true,
        backends: [],
        gpu_profiles: [{ id: 'generic-safe', available: true }],
        base_models: [{ id: 'model-1', local: true, compatible_backends: ['mock'] }],
        limits: {},
      } })),
      listDatasets: vi.fn(() => of({ datasets: [{
        dataset_id: 'dataset-1', name: 'Dataset', status: 'valid', format: 'instruction',
        record_count: 12, train_record_count: 10, validation_record_count: 2, size_bytes: 100,
      }] })),
    };
    TestBed.configureTestingModule({ providers: [
      VpModelTrainingOptionsService,
      { provide: AgentDirectoryService, useValue: directory },
      { provide: ModelTrainingApiService, useValue: api },
    ] });

    const result = await firstValueFrom(TestBed.inject(VpModelTrainingOptionsService).load());

    expect(api.capabilities).toHaveBeenCalledWith('http://hub.test');
    expect(api.listDatasets).toHaveBeenCalledWith('http://hub.test', { limit: 100 });
    expect(result.datasets[0].id).toBe('dataset-1');
    expect(result.trainingProfiles[0].id).toBe('generic-safe');
    expect(result.baseModels[0].id).toBe('model-1');
  });

  it('does not address a worker directly when no Hub exists', async () => {
    const api = { capabilities: vi.fn(), listDatasets: vi.fn() };
    TestBed.configureTestingModule({ providers: [
      VpModelTrainingOptionsService,
      { provide: AgentDirectoryService, useValue: { list: () => [{ name: 'worker', role: 'worker', url: 'http://worker.test' }] } },
      { provide: ModelTrainingApiService, useValue: api },
    ] });

    const result = await firstValueFrom(TestBed.inject(VpModelTrainingOptionsService).load());

    expect(result).toEqual({ hubAvailable: false, datasets: [], trainingProfiles: [], baseModels: [] });
    expect(api.capabilities).not.toHaveBeenCalled();
  });
});
