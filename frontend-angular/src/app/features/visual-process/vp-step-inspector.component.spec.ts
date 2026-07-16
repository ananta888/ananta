import { ɵresolveComponentResources, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { VpGraph, VpRuntimeOverlay } from './visual-process-api.service';
import { VpStepInspectorComponent } from './vp-step-inspector.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

function graph(): VpGraph {
  return {
    id: 'vp-1', name: 'Training', description: '', version: '1', tags: [], edges: [],
    steps: [{
      id: 'train', label: 'LoRA trainieren', kind: 'ml_intern_train_lora', gate: true,
      io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [],
      metadata: {
        dataset_id: 'dataset-1', training_profile_id: 'generic-safe', base_model: 'model-1',
        dataset_path: 'legacy/train.jsonl', dataset_root: '/legacy/root', output_dir: 'legacy-output',
      },
    }],
  };
}

function buildGraph(): VpGraph {
  return {
    id: 'vp-build', name: 'Dataset Build', description: '', version: '1', tags: [], edges: [],
    steps: [{
      id: 'build', label: 'Dataset katalogisieren', kind: 'ml_intern_build_lora_dataset', gate: false,
      io: { inputs: [], outputs: [] }, position: { x: 0, y: 0 }, policy_hints: [],
      metadata: {
        name: 'Kuratierte VP-Daten', dataset_id: 'dataset-1',
        dataset_root: '/legacy/root', source_paths: ['legacy/train.jsonl'], output_path: 'legacy-output.jsonl',
      },
    }],
  };
}

describe('VpStepInspectorComponent LoRA integration', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [VpStepInspectorComponent] }).compileComponents();
  });

  function create(runtimeOverlay: VpRuntimeOverlay | null = null, value: VpGraph = graph()) {
    const fixture = TestBed.createComponent(VpStepInspectorComponent);
    const component = fixture.componentInstance;
    component.graph = signal(value);
    component.selectedId = signal(value.steps[0].id);
    component.taskKindList = signal([
      {
        id: 'ml_intern_train_lora', label: 'LoRA', group: 'ml', dispatch_capable: false,
        description: '', implementation_status: 'production', implementation_state: 'wired_and_executable',
      },
      {
        id: 'ml_intern_build_lora_dataset', label: 'LoRA Dataset', group: 'ml', dispatch_capable: false,
        description: '', implementation_status: 'production', implementation_state: 'wired_and_executable',
      },
    ]);
    component.skillProfiles = signal([]);
    component.modelProfiles = signal([]);
    component.fallbackGroups = signal({});
    component.trainingDatasets = [{
      id: 'dataset-1', name: 'Kuratierte Daten', format: 'instruction', status: 'valid', size_bytes: 10,
      record_count: 12, train_record_count: 10, validation_record_count: 2, trainable: true,
    }];
    component.trainingProfiles = [{ id: 'generic-safe', label: 'Generic safe', available: true }];
    component.trainingBaseModels = [{ id: 'model-1', label: 'Local model', local: true, available: true, compatible_backends: ['mock'] }];
    component.artifactKinds = [];
    component.edgeKinds = [];
    component.encodingModes = [];
    component.ragChannels = [];
    component.runtimeOverlay = runtimeOverlay;
    fixture.detectChanges();
    fixture.detectChanges();
    return fixture;
  }

  it('uses catalog selects and exposes legacy path fields only as deprecated values', () => {
    const fixture = create();
    const element = fixture.nativeElement as HTMLElement;

    expect(fixture.componentInstance.trainingDatasetId()).toBe('dataset-1');
    expect(fixture.componentInstance.trainingProfileId()).toBe('generic-safe');
    expect(element.querySelector('[data-testid="vp-training-dataset"] option[value="dataset-1"]')).not.toBeNull();
    expect(element.querySelector('[data-testid="vp-training-profile"] option[value="generic-safe"]')).not.toBeNull();
    expect(element.textContent).toContain('Deprecated:');
    expect(element.textContent).toContain('dataset_path');
    expect(element.textContent).toContain('legacy/train.jsonl');
    expect(element.textContent).toContain('Training-Control-Center');
    expect(element.querySelector('input[placeholder*="train.jsonl"]')).toBeNull();
  });

  it('renders the runtime job ID, phase, terminal result and Control-Center links', () => {
    const overlay: VpRuntimeOverlay = {
      run_id: 'run-1', workflow_id: 'wf-1', overall_status: 'done', current_step_ids: [], updated_at: 1,
      steps: {
        train: {
          step_id: 'train', status: 'succeeded', training: {
            jobId: 'job-17', datasetId: 'dataset-1', status: 'completed', phase: 'publishing',
            trainingProfileId: 'generic-safe', terminal: true, terminalResult: { adapter_id: 'adapter-1' },
            modelTrainingUrl: '/model-training', jobUrl: '/model-training?tab=jobs&job_id=job-17',
            datasetUrl: '/model-training?tab=datasets&dataset_id=dataset-1',
          },
        },
      },
    };
    const element = create(overlay).nativeElement as HTMLElement;

    expect(element.textContent).toContain('job-17');
    expect(element.textContent).toContain('publishing');
    expect(element.textContent).toContain('adapter-1');
    expect(element.querySelector('a[href="/model-training?tab=jobs&job_id=job-17"]')).not.toBeNull();
  });

  it('uses catalog selection for dataset builds and renders legacy path fields read-only', () => {
    const element = create(null, buildGraph()).nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="vp-dataset-build-source"] option[value="dataset-1"]')).not.toBeNull();
    expect(element.textContent).toContain('bounded Records');
    expect(element.textContent).toContain('Deprecated und nur lesbar:');
    expect(element.textContent).toContain('/legacy/root');
    expect(element.textContent).toContain('legacy/train.jsonl');
    expect(element.textContent).toContain('legacy-output.jsonl');
    expect(element.textContent).not.toContain('Dataset-Root');
    expect(element.textContent).not.toContain('Quell-Dateien');
    expect(element.textContent).not.toContain('Output JSONL');
    expect(element.querySelector('input[placeholder*="train.jsonl"]')).toBeNull();
  });

  it('renders the canonical dataset-build result and Control-Center link', () => {
    const overlay: VpRuntimeOverlay = {
      run_id: 'run-build', workflow_id: 'wf-build', overall_status: 'done', current_step_ids: [], updated_at: 1,
      steps: {
        build: {
          step_id: 'build', status: 'succeeded', datasetBuild: {
            datasetId: 'dataset-1', status: 'validated', validationStatus: 'passed', trainable: true,
            recordCount: 12, trainRecordCount: 10, validationRecordCount: 2,
            sourceMode: 'bounded_upstream_records', modelTrainingUrl: '/model-training',
            datasetUrl: '/model-training?tab=datasets&dataset_id=dataset-1',
          },
        },
      },
    };
    const element = create(overlay, buildGraph()).nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="vp-dataset-build-runtime"]')?.textContent).toContain('dataset-1');
    expect(element.querySelector('[data-testid="vp-dataset-build-runtime"]')?.textContent).toContain('10 / 2');
    expect(element.querySelector('a[href="/model-training?tab=datasets&dataset_id=dataset-1"]')).not.toBeNull();
  });
});
