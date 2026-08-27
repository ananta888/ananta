import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ModelTrainingApiService } from './model-training-api.service';
import { ModelTrainingJobMonitorService } from './model-training-job-monitor.service';
import { ModelTrainingFacade } from './model-training.facade';

describe('ModelTrainingFacade', () => {
  const monitor = { start: vi.fn(), stop: vi.fn(), refresh: vi.fn() };
  let api: Record<string, ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    api = {
      capabilities: vi.fn(() => of({ available: true, backends: [], gpu_profiles: [], base_models: [], limits: {} })),
      recommendBackend: vi.fn(() => of({ status: 'ok', data: {
        schema_version: 'ananta.ml-intern-backend-recommendation.v1', mode: 'recommendation', backend: 'axolotl',
      } })),
      unslothStorage: vi.fn(() => of({
        usage: {
          schema: 'ananta.unsloth-storage-usage.v1',
          catalog_revision: 2,
          usage: { export: { bytes: 32, artifacts: 1 } },
          tenant_total_bytes: 32,
          quotas: {
            dataset_bytes: 100,
            model_bytes: 100,
            checkpoint_bytes: 100,
            export_bytes: 100,
            tenant_total_bytes: 400,
            retention_seconds: 3600,
            max_cleanup_items: 10,
          },
          paths_exposed: false,
        },
        items: [{
          artifact_id: 'artifact-storage-1',
          kind: 'export',
          job_id: 'job-1',
          attempt_id: 'attempt-1',
          sha256: 'a'.repeat(64),
          size_bytes: 32,
          state: 'active',
          reference_kinds: [],
          referenced: false,
        }],
      })),
      listDatasets: vi.fn(() => of({ items: [{
        id: 'dataset-1', name: 'Dataset', format: 'instruction', status: 'valid', size_bytes: 10,
        record_count: 10, train_record_count: 8, validation_record_count: 2,
      }], count: 1 })),
      listJobs: vi.fn(() => of({ items: [{
        id: 'job-1', status: 'queued', dataset_id: 'dataset-1', base_model_id: 'model-1', backend: 'mock',
      }], count: 1 })),
      listAdapters: vi.fn(() => of({ items: [{
        id: 'adapter-1', name: 'Adapter', version: 1, base_model_id: 'model-1', status: 'trained',
      }], count: 1 })),
      getDataset: vi.fn(() => of({
        id: 'dataset-1', name: 'Dataset', format: 'instruction', status: 'valid', size_bytes: 10,
        record_count: 10, train_record_count: 8, validation_record_count: 2,
      })),
      listDatasetRecords: vi.fn(() => of({ items: [{ index: 1, instruction: 'x', output: 'y' }], count: 1 })),
      attachValidationDataset: vi.fn(() => of({
        id: 'dataset-1', name: 'Dataset', format: 'instruction', status: 'valid', size_bytes: 10,
        record_count: 10, train_record_count: 8, validation_record_count: 4,
        external_validation: { dataset_id: 'dataset-2', semantic_overlap_count: 0, algorithm_version: 'external-validation-dataset-v1' },
      })),
      deleteDataset: vi.fn(() => of({ id: 'dataset-1', deleted: true })),
    };
    vi.clearAllMocks();
    TestBed.configureTestingModule({ providers: [
      ModelTrainingFacade,
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub.test/' }] } },
      { provide: ModelTrainingApiService, useValue: api },
      { provide: ModelTrainingJobMonitorService, useValue: monitor },
    ] });
  });

  it('loads normalized overview state exclusively from the resolved Hub', () => {
    const facade = TestBed.inject(ModelTrainingFacade);
    facade.loadOverview();

    expect(facade.hubUrl()).toBe('http://hub.test');
    expect(facade.datasetCount()).toBe(1);
    expect(facade.jobCount()).toBe(1);
    expect(facade.adapters()[0].id).toBe('adapter-1');
    expect(api.capabilities).toHaveBeenCalledWith('http://hub.test');
    expect(api.unslothStorage).toHaveBeenCalledWith('http://hub.test');
    expect(facade.unslothStorage()?.usage.catalog_revision).toBe(2);
    expect(api.listDatasets).toHaveBeenCalledWith('http://hub.test', {});
  });

  it('selects a dataset and populates the bounded preview state', () => {
    const facade = TestBed.inject(ModelTrainingFacade);
    facade.selectDataset('dataset-1');

    expect(api.getDataset).toHaveBeenCalledWith('http://hub.test', 'dataset-1');
    expect(api.listDatasetRecords).toHaveBeenCalledWith('http://hub.test', 'dataset-1', 'train', '');
    expect(facade.selectedDataset()?.id).toBe('dataset-1');
    expect(facade.records()).toHaveLength(1);
  });

  it('keeps a recoverable 503 error visible and clearable', () => {
    api.listJobs.mockReturnValueOnce(throwError(() => ({ status: 503 })));
    const facade = TestBed.inject(ModelTrainingFacade);

    facade.loadJobs();

    expect(facade.error()).toContain('vorübergehend nicht verfügbar');
    facade.clearError();
    expect(facade.error()).toBe('');
  });

  it('updates selected validation state and clears deleted dataset state before catalog refresh', () => {
    const facade = TestBed.inject(ModelTrainingFacade);
    facade.selectDataset('dataset-1');

    facade.attachValidationDataset('dataset-1', 'dataset-2', 'attach-key').subscribe();
    expect(api.attachValidationDataset).toHaveBeenCalledWith(
      'http://hub.test', 'dataset-1', { validation_dataset_id: 'dataset-2' }, 'attach-key',
    );
    expect(facade.selectedDataset()?.external_validation?.dataset_id).toBe('dataset-2');
    expect(api.listDatasetRecords).toHaveBeenLastCalledWith('http://hub.test', 'dataset-1', 'validation', '');

    facade.deleteDataset('dataset-1', 'delete-key').subscribe();
    expect(api.deleteDataset).toHaveBeenCalledWith('http://hub.test', 'dataset-1', 'delete-key');
    expect(facade.selectedDataset()).toBeNull();
    expect(facade.records()).toEqual([]);
    expect(api.listDatasets).toHaveBeenCalled();
  });
});
