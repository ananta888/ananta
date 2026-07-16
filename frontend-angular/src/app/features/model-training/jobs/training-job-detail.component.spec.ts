import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { TrainingJobDetail } from '../model-training.models';
import { TrainingJobDetailComponent } from './training-job-detail.component';

describe('TrainingJobDetailComponent', () => {
  const currentJob = signal<TrainingJobDetail | null>(job());
  const monitor = {
    job: currentJob,
    events: signal([{ sequence: 1, event_type: 'log', message: 'Bearer super-secret password=hunter2' }]),
    mode: signal<'idle' | 'polling'>('polling'),
    error: signal(''),
    refresh: vi.fn(),
  };
  const facade = {
    monitor,
    cancelJob: vi.fn(() => of(job({ status: 'cancel_requested' }))),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    currentJob.set(job());
    TestBed.configureTestingModule({
      imports: [TrainingJobDetailComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('offers cancellation only for cancellable, non-terminal jobs', () => {
    const component = TestBed.createComponent(TrainingJobDetailComponent).componentInstance;
    expect(component.canCancel()).toBe(true);

    currentJob.set(job({ status: 'cancel_requested' }));
    expect(component.canCancel()).toBe(false);
    currentJob.set(job({ status: 'completed' }));
    expect(component.canCancel()).toBe(false);
    currentJob.set(job({ cancellable: false }));
    expect(component.canCancel()).toBe(false);
  });

  it('sends a reason-bound idempotent cancellation request', () => {
    const component = TestBed.createComponent(TrainingJobDetailComponent).componentInstance;
    component.cancelReason = 'Operator requested cancellation';

    component.cancel();

    expect(facade.cancelJob).toHaveBeenCalledWith(
      'job-1',
      'Operator requested cancellation',
      expect.stringMatching(/^training-cancel-/),
    );
  });

  it('distinguishes requested, cooperative and forced cancellation and redacts logs', () => {
    const component = TestBed.createComponent(TrainingJobDetailComponent).componentInstance;

    expect(component.cancellationLabel(job({ status: 'cancel_requested' }))).toContain('angefragt');
    expect(component.cancellationLabel(job({ status: 'cancelled', cancel_mode: 'cooperative' }))).toContain('kooperativ');
    expect(component.cancellationLabel(job({ status: 'cancelled', cancel_mode: 'forced' }))).toContain('erzwungen');
    const message = component.eventMessage(monitor.events()[0]);
    expect(message).not.toContain('super-secret');
    expect(message).not.toContain('hunter2');
  });
});

function job(overrides: Partial<TrainingJobDetail> = {}): TrainingJobDetail {
  return {
    id: 'job-1', status: 'running', phase: 'train', dataset_id: 'dataset-1', base_model_id: 'model-1',
    backend: 'peft', metrics: [], cancellable: true, ...overrides,
  };
}
