import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { of, Subject } from 'rxjs';

import { ModelTrainingFacade } from '../model-training.facade';
import { TrainingCapabilities, TrainingJobAcceptance } from '../model-training.models';
import { TrainingWizardComponent } from './training-wizard.component';

describe('TrainingWizardComponent', () => {
  const datasets = signal([validDataset()]);
  const capabilities = signal<TrainingCapabilities | null>(availableCapabilities());
  const accepted = new Subject<TrainingJobAcceptance>();
  const facade = {
    datasets,
    capabilities,
    createJob: vi.fn(() => accepted.asObservable()),
    recommendBackend: vi.fn(() => of({
      schema_version: 'ananta.ml-intern-backend-recommendation.v1',
      mode: 'recommendation',
      backend: 'axolotl',
      requires_confirmation: true,
      reasons: ['worker_capability_available'],
      capability_evidence: { source: 'current_worker_probe_and_hub_policy', backend_version: '0.18.0', available: true },
      estimated_resources: { model_bytes: 0, runtime_budget_seconds: 3600, profile: 'gpu-1', estimate_only: true },
      alternatives: [],
      fallback_policy: 'new_visible_attempt_only',
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    datasets.set([validDataset()]);
    capabilities.set(availableCapabilities());
    TestBed.configureTestingModule({
      imports: [TrainingWizardComponent],
      providers: [{ provide: ModelTrainingFacade, useValue: facade }],
    });
  });

  it('starts in safe dry-run mode and submits one idempotent Hub job while pending', () => {
    const fixture = TestBed.createComponent(TrainingWizardComponent);
    const component = fixture.componentInstance;
    component.activeIndex = component.steps.length - 1;
    component.datasetId = 'dataset-1';
    component.baseModelId = 'model-1';
    component.backend = 'mock';
    component.gpuProfile = 'none';

    expect(component.mode).toBe('dry_run');
    expect(component.canContinue()).toBe(true);

    component.submit();
    component.submit();

    expect(facade.createJob).toHaveBeenCalledTimes(1);
    expect(facade.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'dry_run', dataset_id: 'dataset-1', require_dataset_validation: true, require_secret_scan: true,
      }),
      expect.stringMatching(/^training-job-/),
    );
    expect(component.busy).toBe(true);

    accepted.next({ job_id: 'job-1', status: 'queued' });
    accepted.complete();
    expect(component.busy).toBe(false);
  });

  it('blocks live training until dataset, runtime, reason and confirmation gates pass', () => {
    datasets.set([{ ...validDataset(), validation_record_count: 0, trainable: false }]);
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    configureLive(component);

    expect(component.blockingReasons()).toContain('Dataset ist nicht validiert oder besitzt keinen Validation-Split.');
    expect(component.canContinue()).toBe(false);

    datasets.set([validDataset()]);
    expect(component.blockingReasons()).toEqual([]);
    expect(component.canContinue()).toBe(false);

    component.riskReason = 'Geprüfter lokaler Trainingslauf';
    component.liveConfirmed = true;
    expect(component.canContinue()).toBe(true);
  });

  it('rejects incompatible or unavailable local runtime selections for live mode', () => {
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    configureLive(component);
    component.backend = 'unsupported';
    component.riskReason = 'Geprüfter lokaler Trainingslauf';
    component.liveConfirmed = true;

    expect(component.blockingReasons()).toContain('Basismodell, Backend oder GPU-Profil sind nicht kompatibel/verfügbar.');
    expect(component.canContinue()).toBe(false);
  });

  it('walks all four steps and mirrors bounded Hub rank/step limits', () => {
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    expect(component.steps.map(step => step.id)).toEqual(['dataset', 'runtime', 'parameters', 'review']);
    component.datasetId = 'dataset-1';
    component.next();
    expect(component.activeIndex).toBe(1);
    component.baseModelId = 'model-1';
    component.backend = 'mock';
    component.gpuProfile = 'none';
    component.next();
    expect(component.activeIndex).toBe(2);

    component.loraRank = 65;
    component.maxTrainingSteps = 1001;
    expect(component.rankError()).toContain('64');
    expect(component.stepsError()).toContain('1000');
    expect(component.canContinue()).toBe(false);

    component.loraRank = 64;
    component.maxTrainingSteps = 1000;
    component.next();
    expect(component.activeIndex).toBe(3);
  });

  it.each([
    ['loraAlpha', 513],
    ['batchSize', 129],
    ['gradientAccumulation', 1025],
    ['maxSequenceLength', 32769],
  ] as const)('blocks programmatic %s values above the Hub capability bound', (field, value) => {
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    component.activeIndex = 2;
    component[field] = value;

    expect(component.canContinue()).toBe(false);
  });

  it('uses the selected GPU profile hard limits in addition to global bounds', () => {
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    component.activeIndex = 2;
    component.gpuProfile = 'gpu-1';
    component.batchSize = 9;
    component.maxSequenceLength = 4096;
    expect(component.maxBatchSize()).toBe(8);
    expect(component.canContinue()).toBe(false);

    component.batchSize = 8;
    component.maxSequenceLength = 4097;
    expect(component.maxSequenceLengthLimit()).toBe(4096);
    expect(component.canContinue()).toBe(false);
  });

  it('shows a Hub recommendation without changing the explicit backend selection', () => {
    const component = TestBed.createComponent(TrainingWizardComponent).componentInstance;
    component.backend = 'peft';
    component.gpuProfile = 'gpu-1';

    component.requestRecommendation();

    expect(facade.recommendBackend).toHaveBeenCalledWith(expect.objectContaining({
      method: 'qlora', resource_profile: 'gpu-1',
    }));
    expect(component.recommendation?.backend).toBe('axolotl');
    expect(component.backend).toBe('peft');
  });
});

function configureLive(component: TrainingWizardComponent): void {
  component.activeIndex = component.steps.length - 1;
  component.mode = 'live';
  component.datasetId = 'dataset-1';
  component.baseModelId = 'model-1';
  component.backend = 'peft';
  component.gpuProfile = 'gpu-1';
}

function validDataset() {
  return {
    id: 'dataset-1', name: 'Training', format: 'instruction' as const, status: 'valid', validation_status: 'valid',
    size_bytes: 10, record_count: 12, train_record_count: 10, validation_record_count: 2, trainable: true,
  };
}

function availableCapabilities(): TrainingCapabilities {
  return {
    available: true,
    backends: [{ id: 'mock', available: true }, { id: 'peft', available: true }],
    gpu_profiles: [
      { id: 'none', available: true, max_batch_size: 128, max_sequence_length: 32_768 },
      { id: 'gpu-1', available: true, max_batch_size: 8, max_sequence_length: 4096 },
    ],
    base_models: [{ id: 'model-1', local: true, available: true, compatible_backends: ['mock', 'peft'] }],
    limits: {
      max_lora_rank: 64,
      max_lora_alpha: 512,
      max_batch_size: 128,
      max_gradient_accumulation_steps: 1024,
      min_sequence_length: 128,
      max_sequence_length: 32_768,
      max_steps: 1_000,
    },
  };
}
