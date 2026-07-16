import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetDetail } from '../model-training.models';
import { DatasetManagementComponent } from './dataset-management.component';

describe('DatasetManagementComponent', () => {
  const selectedDataset = signal<DatasetDetail | null>(dataset('train-1', 'Training'));
  const datasets = signal<DatasetDetail[]>([
    dataset('train-1', 'Training'), dataset('validation-2', 'Separate Validation'),
  ]);
  const facade = {
    selectedDataset,
    datasets,
    attachValidationDataset: vi.fn(() => of({
      ...dataset('train-1', 'Training'),
      external_validation: {
        dataset_id: 'validation-2', semantic_overlap_count: 0, algorithm_version: 'external-validation-dataset-v1',
      },
    })),
    deleteDataset: vi.fn(() => of({ id: 'train-1', deleted: true as const })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedDataset.set(dataset('train-1', 'Training'));
    TestBed.configureTestingModule({
      imports: [DatasetManagementComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('attaches only another uploaded dataset after explicit confirmation', () => {
    const component = TestBed.createComponent(DatasetManagementComponent).componentInstance;
    expect(component.validationCandidates().map(item => item.id)).toEqual(['validation-2']);
    component.validationDatasetId = 'validation-2';
    component.attachConfirmed = true;

    component.attachValidationDataset();

    expect(facade.attachValidationDataset).toHaveBeenCalledWith(
      'train-1', 'validation-2', expect.stringMatching(/^dataset-external-validation-/),
    );
    expect(component.attachMessage).toContain('validation-2');
    expect(component.attachConfirmed).toBe(false);
  });

  it('keeps a dataset_referenced 409 visible and never invents a force flag', () => {
    facade.deleteDataset.mockReturnValueOnce(throwError(() => ({
      status: 409,
      error: { data: { error: { code: 'dataset_referenced', message: 'referenced datasets cannot be deleted' } } },
    })) as any);
    const component = TestBed.createComponent(DatasetManagementComponent).componentInstance;
    component.deleteConfirmed = true;

    component.deleteDataset();

    expect(facade.deleteDataset).toHaveBeenCalledWith('train-1', expect.stringMatching(/^dataset-delete-/));
    expect(component.deleteError).toContain('dataset_referenced');
    expect(component.deleteError).toContain('Force-Delete wird nicht angeboten');
    expect(facade.deleteDataset.mock.calls[0]).toHaveLength(2);
  });
});

function dataset(id: string, name: string): DatasetDetail {
  return {
    id, name, format: 'instruction', status: 'valid', validation_status: 'valid', trainable: true,
    size_bytes: 10, record_count: 10, train_record_count: 8, validation_record_count: 2,
  };
}
