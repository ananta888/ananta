import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterSummary, EvaluationReport } from '../model-training.models';
import { AdapterRegistryComponent } from './adapter-registry.component';
import { BrowserArtifactDownloadService } from './browser-artifact-download.service';

describe('AdapterRegistryComponent', () => {
  const selectedAdapter = signal<AdapterSummary | null>(adapter());
  const selectedEvaluation = signal<EvaluationReport | null>(evaluation(true));
  const facade = {
    adapters: signal<AdapterSummary[]>([adapter()]),
    selectedAdapter,
    selectedEvaluation,
    loadingAdapters: signal(false),
    loadAdapters: vi.fn(),
    selectAdapter: vi.fn(),
    decideAdapter: vi.fn(() => of(adapter({ status: 'approved', version: 4 }))),
    exportAdapter: vi.fn(() => of({ artifact_id: 'artifact-1', sha256: 'abc' })),
    downloadAdapterExport: vi.fn(() => of({
      blob: new Blob(['zip'], { type: 'application/zip' }),
      filename: 'artifact-1.zip',
      sha256: 'abc',
    })),
  };
  const downloads = { save: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    selectedAdapter.set(adapter());
    selectedEvaluation.set(evaluation(true));
    TestBed.configureTestingModule({
      imports: [AdapterRegistryComponent],
      providers: [
        { provide: ModelTrainingFacade, useValue: facade },
        { provide: BrowserArtifactDownloadService, useValue: downloads },
      ],
    });
  });

  it('requires evaluated status and a passing evaluation before approval', () => {
    const component = TestBed.createComponent(AdapterRegistryComponent).componentInstance;
    expect(component.canAction(adapter(), 'approve')).toBe(true);

    selectedEvaluation.set(evaluation(false));
    expect(component.canAction(adapter(), 'approve')).toBe(false);
    expect(component.canAction(adapter({ status: 'trained' }), 'approve')).toBe(false);
  });

  it('binds lifecycle decisions to confirmation, reason and expected adapter version', () => {
    const component = TestBed.createComponent(AdapterRegistryComponent).componentInstance;
    component.action = 'approve';
    component.reason = 'Evaluation passed';
    component.confirmed = true;

    component.decide();

    expect(facade.decideAdapter).toHaveBeenCalledWith(
      'adapter-1',
      'approve',
      { reason: 'Evaluation passed', expected_version: 8, confirmed: true },
      expect.stringMatching(/^adapter-approve-/),
    );
  });

  it('allows export only for an existing, hash-verified artifact', () => {
    const component = TestBed.createComponent(AdapterRegistryComponent).componentInstance;

    expect(component.canExport(adapter())).toBe(true);
    expect(component.canExport(adapter({ hash_verified: false }))).toBe(false);
    expect(component.canExport(adapter({ artifact_exists: false }))).toBe(false);
    expect(component.canExport(adapter({ status: 'trained' }))).toBe(false);

    component.exportAdapter();
    expect(facade.exportAdapter).toHaveBeenCalledWith('adapter-1', expect.stringMatching(/^adapter-export-/));
  });

  it('downloads the prepared export with Hub auth and enforces the announced hash', () => {
    const component = TestBed.createComponent(AdapterRegistryComponent).componentInstance;
    component.exportAdapter();
    component.downloadExport();

    expect(facade.downloadAdapterExport).toHaveBeenCalledWith('artifact-1');
    expect(downloads.save).toHaveBeenCalledWith(expect.objectContaining({
      filename: 'artifact-1.zip',
      sha256: 'abc',
    }));

    downloads.save.mockClear();
    facade.downloadAdapterExport.mockReturnValueOnce(of({
      blob: new Blob(['tampered']), filename: 'artifact-1.zip', sha256: 'def',
    }));
    component.downloadExport();
    expect(downloads.save).not.toHaveBeenCalled();
    expect(component.error()).toContain('SHA-256');
  });

  it.each([
    ['reject', 'evaluated', true],
    ['reject', 'trained', false],
    ['deprecate', 'approved', true],
    ['deprecate', 'rejected', true],
    ['rollback', 'approved', true],
    ['rollback', 'deprecated', true],
    ['rollback', 'evaluated', false],
  ] as const)('matches server lifecycle matrix for %s from %s', (action, status, expected) => {
    const component = TestBed.createComponent(AdapterRegistryComponent).componentInstance;
    expect(component.canAction(adapter({ status }), action)).toBe(expected);
  });
});

function adapter(overrides: Partial<AdapterSummary> = {}): AdapterSummary {
  return {
    id: 'adapter-1', name: 'Adapter', version: 3, registry_version: 8,
    base_model_id: 'model-1', status: 'evaluated',
    hash_verified: true, artifact_exists: true, ...overrides,
  };
}

function evaluation(passed: boolean): EvaluationReport {
  return {
    id: 'eval-1', adapter_id: 'adapter-1', dataset_id: 'dataset-1', status: 'completed',
    passed, metrics: [], samples: [],
  };
}
