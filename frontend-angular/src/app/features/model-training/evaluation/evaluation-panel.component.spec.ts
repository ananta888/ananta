import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterSummary, DatasetSummary, EvaluationReport } from '../model-training.models';
import { EvaluationPanelComponent } from './evaluation-panel.component';

describe('EvaluationPanelComponent', () => {
  const report: EvaluationReport = {
    id: 'evaluation-1', adapter_id: 'adapter-1', dataset_id: 'dataset-1', status: 'completed', passed: true,
    metrics: [{ name: 'accuracy', base_value: 0.4, adapter_value: 0.8, delta: 0.4 }],
    samples: [{ id: 'sample-1', base_output: 'base', adapter_output: 'adapter', winner: 'adapter' }],
  };
  const selectedAdapter = signal<AdapterSummary | null>({
    id: 'adapter-1', name: 'Adapter', version: 1, base_model_id: 'model-1', status: 'evaluated',
  });
  const datasets = signal<DatasetSummary[]>([{
    id: 'dataset-1', name: 'Valid', format: 'instruction', status: 'valid', validation_status: 'valid',
    size_bytes: 1, record_count: 10, train_record_count: 8, validation_record_count: 2, trainable: true,
  }, {
    id: 'dataset-2', name: 'No validation', format: 'instruction', status: 'uploaded',
    size_bytes: 1, record_count: 10, train_record_count: 10, validation_record_count: 0,
  }]);
  const facade = {
    selectedAdapter,
    datasets,
    capabilities: signal({ available: true, mode: 'dry_run' }),
    selectedEvaluation: signal<EvaluationReport | null>(null),
    evaluateAdapter: vi.fn(() => of(report)),
    loadEvaluation: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    facade.capabilities.set({ available: true, mode: 'dry_run' });
    TestBed.configureTestingModule({
      imports: [EvaluationPanelComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('offers only validated datasets and requests a bounded Base-vs-Adapter evaluation', () => {
    const component = TestBed.createComponent(EvaluationPanelComponent).componentInstance;
    expect(component.eligibleDatasets().map(item => item.id)).toEqual(['dataset-1']);
    component.datasetId = 'dataset-1';
    component.scorerName = 'ananta_todo_json';

    component.evaluate();

    expect(facade.evaluateAdapter).toHaveBeenCalledWith(
      'adapter-1', 'dataset-1', 'ananta_todo_json', false, '',
      expect.stringMatching(/^adapter-evaluation-/),
    );
  });

  it('requires explicit reason and confirmation for live evaluation', () => {
    facade.capabilities.set({ available: true, mode: 'live' });
    const component = TestBed.createComponent(EvaluationPanelComponent).componentInstance;
    component.datasetId = 'dataset-1';
    expect(component.canEvaluate()).toBe(false);

    component.riskReason = 'Geprüfte lokale Adapterevaluation';
    component.liveConfirmed = true;
    component.evaluate();

    expect(facade.evaluateAdapter).toHaveBeenCalledWith(
      'adapter-1', 'dataset-1', 'generic', true, 'Geprüfte lokale Adapterevaluation',
      expect.stringMatching(/^adapter-evaluation-/),
    );
  });

  it('offers only the two canonical scorer contracts and defaults safely to generic', () => {
    const fixture = TestBed.createComponent(EvaluationPanelComponent);
    fixture.detectChanges();
    const scorer = fixture.nativeElement.querySelectorAll('select')[1] as HTMLSelectElement;

    expect(fixture.componentInstance.scorerName).toBe('generic');
    expect(Array.from(scorer.options).map(option => option.value)).toEqual(['generic', 'ananta_todo_json']);
  });

  it('keeps a visible retry instruction when evaluation service is unavailable', () => {
    facade.evaluateAdapter.mockReturnValueOnce(throwError(() => ({ status: 503 })) as any);
    const component = TestBed.createComponent(EvaluationPanelComponent).componentInstance;
    component.datasetId = 'dataset-1';

    component.evaluate();

    expect(component.error).toContain('vorübergehend nicht verfügbar');
    expect(component.error).toContain('erneut versuchen');
  });
});
