import { describe, expect, it } from 'vitest';

import {
  extractVpDatasetBuildRuntime,
  extractVpTrainingRuntime,
  stringifyVpRuntimeResult,
} from './vp-model-training-contract';

describe('VP model-training runtime contract', () => {
  it('normalizes adapter outputs including canonical Control-Center links', () => {
    const result = extractVpTrainingRuntime({
      result: {
        outputs: {
          job_id: 'job 17',
          dataset_id: 'dataset/9',
          training_profile_id: 'generic-safe',
          training_status: 'completed',
          training_phase: 'publish',
          terminal: true,
          terminal_result: { adapter_id: 'adapter-1' },
        },
      },
    });

    expect(result).toEqual(expect.objectContaining({
      jobId: 'job 17',
      datasetId: 'dataset/9',
      trainingProfileId: 'generic-safe',
      status: 'completed',
      phase: 'publish',
      terminal: true,
      jobUrl: '/model-training?tab=jobs&job_id=job%2017',
      datasetUrl: '/model-training?tab=datasets&dataset_id=dataset%2F9',
    }));
  });

  it('rejects external or misleading runtime links and rebuilds bounded links', () => {
    const result = extractVpTrainingRuntime({
      outputs: {
        job_id: 'job-1', dataset_id: 'dataset-1', status: 'running',
        job_url: 'https://attacker.invalid/job',
        dataset_url: '//attacker.invalid/model-training',
        model_training_url: '/model-training-evil',
      },
    });

    expect(result?.modelTrainingUrl).toBe('/model-training');
    expect(result?.jobUrl).toBe('/model-training?tab=jobs&job_id=job-1');
    expect(result?.datasetUrl).toBe('/model-training?tab=datasets&dataset_id=dataset-1');
    expect(result?.terminal).toBe(false);
  });

  it('bounds terminal result rendering', () => {
    expect(stringifyVpRuntimeResult({ value: 'x'.repeat(5000) }).length).toBeLessThan(4100);
  });

  it('normalizes canonical dataset-build outputs without accepting an external link', () => {
    const result = extractVpDatasetBuildRuntime({
      execution_result: {
        outputs: {
          dataset_id: 'dataset/17',
          dataset_status: 'validated',
          dataset_url: 'https://attacker.invalid/dataset',
          dataset_build_result: {
            id: 'dataset/17', validation_status: 'passed', trainable: true,
            record_count: 12, train_record_count: 10, validation_record_count: 2,
          },
        },
        diagnostics: { source_mode: 'bounded_upstream_records' },
      },
    });

    expect(result).toEqual({
      datasetId: 'dataset/17',
      status: 'validated',
      validationStatus: 'passed',
      trainable: true,
      recordCount: 12,
      trainRecordCount: 10,
      validationRecordCount: 2,
      sourceMode: 'bounded_upstream_records',
      modelTrainingUrl: '/model-training',
      datasetUrl: '/model-training?tab=datasets&dataset_id=dataset%2F17',
    });
  });
});
