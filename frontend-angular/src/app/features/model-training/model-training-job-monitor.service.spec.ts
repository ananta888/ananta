import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';

import { ModelTrainingApiService } from './model-training-api.service';
import { ModelTrainingJobMonitorService } from './model-training-job-monitor.service';
import { TrainingJobDetail } from './model-training.models';

describe('ModelTrainingJobMonitorService', () => {
  const api = {
    getJob: vi.fn(),
    listJobEvents: vi.fn(),
    streamJobEvents: undefined as undefined | ReturnType<typeof vi.fn>,
  };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    api.streamJobEvents = undefined;
    api.getJob.mockReturnValue(of(job('running')));
    api.listJobEvents.mockImplementation((_hub: string, _jobId: string, after: number) => of(after === 0
      ? { items: [event(2), event(1)], count: 2, next_sequence: 2 }
      : { items: [event(2), event(3)], count: 2, next_sequence: 3 }));
    TestBed.configureTestingModule({
      providers: [
        ModelTrainingJobMonitorService,
        { provide: ModelTrainingApiService, useValue: api },
      ],
    });
  });

  afterEach(() => {
    TestBed.inject(ModelTrainingJobMonitorService).stop();
    vi.useRealTimers();
  });

  it('polls Hub job state with a cursor and de-duplicates ordered events', async () => {
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);
    monitor.start('http://hub.test', 'job-1');

    await vi.advanceTimersByTimeAsync(0);
    expect(api.getJob).toHaveBeenCalledWith('http://hub.test', 'job-1');
    expect(api.listJobEvents).toHaveBeenNthCalledWith(1, 'http://hub.test', 'job-1', 0, 200);
    expect(monitor.events().map(item => item.sequence)).toEqual([1, 2]);
    expect(monitor.connected()).toBe(true);

    await vi.advanceTimersByTimeAsync(3_000);
    expect(api.listJobEvents).toHaveBeenNthCalledWith(2, 'http://hub.test', 'job-1', 2, 200);
    expect(monitor.events().map(item => item.sequence)).toEqual([1, 2, 3]);
  });

  it('stops polling after a terminal Hub status', async () => {
    api.getJob.mockReturnValue(of(job('completed')));
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);

    monitor.start('http://hub.test', 'job-2');
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(12_000);

    expect(api.getJob).toHaveBeenCalledTimes(1);
    expect(monitor.job()?.status).toBe('completed');
  });

  it('keeps monitoring an interrupted job so Hub recovery can requeue it', async () => {
    api.getJob.mockReturnValueOnce(of(job('interrupted'))).mockReturnValue(of(job('queued')));
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);

    monitor.start('http://hub.test', 'job-recovering');
    await vi.advanceTimersByTimeAsync(0);
    expect(monitor.job()?.status).toBe('interrupted');

    await vi.advanceTimersByTimeAsync(3_000);
    expect(api.getJob).toHaveBeenCalledTimes(2);
    expect(monitor.job()?.status).toBe('queued');
    expect(monitor.mode()).toBe('polling');
  });

  it('does not start a second polling loop for the same active job', async () => {
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);
    monitor.start('http://hub.test', 'job-1');
    monitor.start('http://hub.test', 'job-1');

    await vi.advanceTimersByTimeAsync(0);

    expect(api.getJob).toHaveBeenCalledTimes(1);
  });

  it('falls back visibly to cursor polling when the event stream fails', async () => {
    api.streamJobEvents = vi.fn(() => throwError(() => new Error('stream unavailable')));
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);

    monitor.start('http://hub.test', 'job-stream');
    await vi.advanceTimersByTimeAsync(0);

    expect(api.streamJobEvents).toHaveBeenCalledWith('http://hub.test', 'job-stream', 0);
    expect(monitor.mode()).toBe('polling');
    expect(monitor.connected()).toBe(true);
  });

  it('does not poll event pages while an authenticated stream remains healthy', async () => {
    api.streamJobEvents = vi.fn(() => new Observable(() => undefined));
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);

    monitor.start('http://hub.test', 'job-streaming');
    await vi.advanceTimersByTimeAsync(6_000);

    expect(monitor.mode()).toBe('streaming');
    expect(api.getJob).toHaveBeenCalledTimes(3);
    expect(api.listJobEvents).not.toHaveBeenCalled();
  });

  it('cleans up both stream and polling subscriptions', async () => {
    const streamTeardown = vi.fn();
    api.streamJobEvents = vi.fn(() => new Observable(() => streamTeardown));
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);
    monitor.start('http://hub.test', 'job-cleanup');
    await vi.advanceTimersByTimeAsync(0);

    monitor.stop();

    expect(streamTeardown).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(6_000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
  });

  it('bounds non-terminal polling to ten minutes and exposes manual recovery', async () => {
    const monitor = TestBed.inject(ModelTrainingJobMonitorService);
    monitor.start('http://hub.test', 'job-long');

    await vi.advanceTimersByTimeAsync(600_000);

    expect(api.getJob).toHaveBeenCalledTimes(201);
    expect(monitor.mode()).toBe('idle');
    expect(monitor.error()).toContain('10 Minuten');
    expect(monitor.error()).toContain('Aktualisieren');
  });
});

function event(sequence: number) {
  return { sequence, event_type: 'metric', message: `step ${sequence}` };
}

function job(status: string): TrainingJobDetail {
  return {
    id: 'job-1', status, phase: status, dataset_id: 'dataset-1', base_model_id: 'model-1',
    backend: 'peft', metrics: [], current_step: 2, max_steps: 10,
  };
}
