import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  ASSISTANT_READ_MODEL_CONTRACT_KEYS,
  DASHBOARD_READ_MODEL_CONTRACT_KEYS,
  TASK_ORCHESTRATION_READ_MODEL_CONTRACT_KEYS,
} from './dashboard.models';

function baselineKeys(path: string): string[] {
  const raw = readFileSync(resolve(process.cwd(), '..', path), 'utf-8');
  return Object.keys(JSON.parse(raw)).sort();
}

describe('frontend read-model contracts', () => {
  it('tracks dashboard read-model top-level baseline keys', () => {
    expect([...DASHBOARD_READ_MODEL_CONTRACT_KEYS].sort()).toEqual(
      baselineKeys('tests/baselines/dashboard_read_model.json'),
    );
  });

  it('tracks assistant read-model top-level baseline keys', () => {
    expect([...ASSISTANT_READ_MODEL_CONTRACT_KEYS].sort()).toEqual(
      baselineKeys('tests/baselines/assistant_read_model.json'),
    );
  });

  it('tracks orchestration read-model top-level baseline keys', () => {
    expect([...TASK_ORCHESTRATION_READ_MODEL_CONTRACT_KEYS].sort()).toEqual(
      baselineKeys('tests/baselines/orchestration_read_model.json'),
    );
  });
});
