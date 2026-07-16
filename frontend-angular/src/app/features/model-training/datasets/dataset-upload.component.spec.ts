import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, throwError } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetDetail, DatasetUploadEvent, TrainingCapabilities } from '../model-training.models';
import { DatasetUploadComponent } from './dataset-upload.component';

describe('DatasetUploadComponent', () => {
  const capabilities = signal<TrainingCapabilities | null>({
    available: true, backends: [], gpu_profiles: [], base_models: [], limits: { max_dataset_bytes: 128 },
  });
  const uploaded = new Subject<DatasetUploadEvent>();
  const facade = {
    capabilities,
    uploadDatasetWithProgress: vi.fn(() => uploaded.asObservable()),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      imports: [DatasetUploadComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('accepts JSONL metadata and prevents duplicate submits while upload is pending', () => {
    const component = TestBed.createComponent(DatasetUploadComponent).componentInstance;
    const file = new File(['{"instruction":"x","output":"y"}'], 'dataset.jsonl');
    component.selectFile({ target: { files: [file] } } as unknown as Event);
    component.purpose = 'Lokale Assistenz';
    component.license = 'private';

    expect(component.canUpload()).toBe(true);
    component.upload();
    component.upload();

    expect(facade.uploadDatasetWithProgress).toHaveBeenCalledTimes(1);
    expect(facade.uploadDatasetWithProgress).toHaveBeenCalledWith(
      expect.objectContaining({ file, validation_ratio: 0.2, split_seed: 42 }),
      expect.stringMatching(/^dataset-upload-/),
    );
    expect(component.busy).toBe(true);

    uploaded.next({ kind: 'progress', loaded: 64, total: 128, percent: 50 });
    expect(component.uploadPercent).toBe(50);
    uploaded.next({ kind: 'complete', dataset: dataset() });
  });

  it('rejects unsupported, empty and over-limit files before any Hub mutation', () => {
    const component = TestBed.createComponent(DatasetUploadComponent).componentInstance;

    component.selectFile({ target: { files: [new File(['x'], 'dataset.csv')] } } as unknown as Event);
    expect(component.fileError).toContain('.json');
    component.selectFile({ target: { files: [new File([], 'empty.json')] } } as unknown as Event);
    expect(component.fileError).toContain('leer');
    component.selectFile({ target: { files: [new File(['x'.repeat(129)], 'large.json')] } } as unknown as Event);
    expect(component.fileError).toContain('Limit');
    expect(facade.uploadDatasetWithProgress).not.toHaveBeenCalled();
  });

  it('renders a specific error for a Hub payload limit rejection', () => {
    facade.uploadDatasetWithProgress.mockReturnValueOnce(throwError(() => ({ status: 413 })) as any);
    const component = TestBed.createComponent(DatasetUploadComponent).componentInstance;
    component.selectFile({ target: { files: [new File(['{}'], 'dataset.json')] } } as unknown as Event);
    component.purpose = 'Lokale Assistenz';
    component.license = 'private';

    component.upload();

    expect(component.error).toContain('Größenlimit');
    expect(component.busy).toBe(false);
  });

  it('renders determinate progress with a polite status announcement', () => {
    const fixture = TestBed.createComponent(DatasetUploadComponent);
    const component = fixture.componentInstance;
    component.file = new File(['{}'], 'dataset.json');
    component.purpose = 'Lokale Assistenz';
    component.license = 'private';
    component.busy = true;
    component.uploadPercent = 42;
    fixture.detectChanges();

    const progress = fixture.nativeElement.querySelector('progress') as HTMLProgressElement;
    expect(progress.value).toBe(42);
    expect(progress.getAttribute('aria-label')).toContain('42 Prozent');
    expect(fixture.nativeElement.querySelector('[role="status"][aria-live="polite"]')).not.toBeNull();
  });
});

function dataset(): DatasetDetail {
  return {
    id: 'dataset-1', name: 'Dataset', format: 'instruction', status: 'valid',
    size_bytes: 2, record_count: 2, train_record_count: 1, validation_record_count: 1,
  };
}
