import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetDetail } from '../model-training.models';
import { DatasetDetailComponent } from './dataset-detail.component';

describe('DatasetDetailComponent', () => {
  const selectedDataset = signal<DatasetDetail | null>(dataset());
  const facade = {
    selectedDataset,
    loadingRecords: signal(false),
    records: signal([]),
    recordSplit: signal<'train' | 'validation'>('train'),
    recordsNextCursor: signal<string | null>(null),
    hasPreviousRecordPage: vi.fn(() => false),
    nextRecordPage: vi.fn(),
    previousRecordPage: vi.fn(),
    loadRecords: vi.fn(),
    splitDataset: vi.fn(() => of(dataset())),
    validateDataset: vi.fn(() => of(dataset().validation_report!)),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedDataset.set(dataset());
    TestBed.configureTestingModule({
      imports: [DatasetDetailComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('shows a deterministic count preview for the configured validation ratio', () => {
    const component = TestBed.createComponent(DatasetDetailComponent).componentInstance;
    component.validationRatio = 0.2;

    expect(component.projectedCounts()).toEqual({ train: 80, validation: 20 });
  });

  it('requires an accessible keyboard-dismissable dialog before replacing an existing split', () => {
    const fixture = TestBed.createComponent(DatasetDetailComponent);
    const component = fixture.componentInstance;

    component.split();
    fixture.detectChanges();
    const dialog = fixture.nativeElement.querySelector('[role="dialog"]') as HTMLElement;

    expect(dialog).not.toBeNull();
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.textContent).toContain('80');
    expect(dialog.textContent).toContain('20');
    expect(facade.splitDataset).not.toHaveBeenCalled();

    dialog.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="dialog"]')).toBeNull();

    component.split();
    component.confirmSplitReplacement();
    expect(facade.splitDataset).toHaveBeenCalledWith(
      'dataset-1', 0.2, 42, true, expect.stringMatching(/^dataset-split-/),
    );
  });

  it('loads train and validation previews through the facade cursor contract', () => {
    const component = TestBed.createComponent(DatasetDetailComponent).componentInstance;

    component.changeSplit('validation');

    expect(facade.loadRecords).toHaveBeenCalledWith('dataset-1', 'validation');
  });
});

function dataset(): DatasetDetail {
  return {
    id: 'dataset-1', name: 'Dataset', format: 'instruction', status: 'valid', validation_status: 'valid',
    size_bytes: 1000, record_count: 100, train_record_count: 80, validation_record_count: 20, trainable: true,
    validation_report: {
      dataset_id: 'dataset-1', valid: true, total_records: 100, accepted_records: 100,
      rejected_records: 0, duplicate_records: 0, secret_findings: 0, pii_findings: 0,
      train_records: 80, validation_records: 20, issues: [],
    },
  };
}
