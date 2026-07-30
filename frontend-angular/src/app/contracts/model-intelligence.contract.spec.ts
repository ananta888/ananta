import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, parse, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  MODEL_INTELLIGENCE_SCHEMAS,
  ModelIntelligenceContractError,
  ModelIntelligenceContractKind,
  parseModelIntelligenceContract,
} from './model-intelligence.contract';

const FIXTURE_DIRECTORY = join('tests', 'fixtures', 'model_intelligence');

type JsonRecord = Record<string, unknown>;

function findRepositoryRoot(startPath: string): string {
  let candidate = resolve(startPath);
  const filesystemRoot = parse(candidate).root;
  while (true) {
    if (
      existsSync(join(candidate, 'frontend-angular', 'package.json'))
      && existsSync(join(candidate, FIXTURE_DIRECTORY))
    ) {
      return candidate;
    }
    if (candidate === filesystemRoot) {
      throw new Error('model_intelligence_fixture_root_not_found');
    }
    candidate = dirname(candidate);
  }
}

function json(path: string): JsonRecord {
  return JSON.parse(readFileSync(path, 'utf8')) as JsonRecord;
}

function record(value: unknown): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('fixture_object_required');
  }
  return value as JsonRecord;
}

const repositoryRoot = findRepositoryRoot(
  dirname(fileURLToPath(import.meta.url)),
);
const validFixture = json(
  join(repositoryRoot, FIXTURE_DIRECTORY, 'contracts.v1.valid.json'),
);
const invalidFixture = json(
  join(repositoryRoot, FIXTURE_DIRECTORY, 'contracts.v1.invalid.json'),
);

describe('model-intelligence cross-language contract v1', () => {
  it('accepts every shared positive golden contract', () => {
    const contracts = record(validFixture['contracts']);
    for (const [kind, payload] of Object.entries(contracts)) {
      const parsed = parseModelIntelligenceContract(
        kind as ModelIntelligenceContractKind,
        payload,
      );
      expect(parsed).toEqual(payload);
      expect(parsed.schema).toBe(
        MODEL_INTELLIGENCE_SCHEMAS[
          kind as ModelIntelligenceContractKind
        ],
      );
    }
  });

  it('rejects every shared negative golden contract with a stable reason', () => {
    const cases = invalidFixture['cases'];
    expect(Array.isArray(cases)).toBe(true);
    for (const rawCase of cases as unknown[]) {
      const testCase = record(rawCase);
      try {
        parseModelIntelligenceContract(
          testCase['contract'] as ModelIntelligenceContractKind,
          testCase['payload'],
        );
        throw new Error('negative_fixture_was_accepted');
      } catch (error) {
        expect(error).toBeInstanceOf(ModelIntelligenceContractError);
        expect(
          (error as ModelIntelligenceContractError).reasonCode,
        ).toBe(testCase['expected_reason_code']);
      }
    }
  });

  it('derives the shared model ID from canonical JSON coordinates', () => {
    const identity = record(
      record(validFixture['contracts'])['model_identity'],
    );
    const canonicalCoordinates = JSON.stringify({
      content_sha256: identity['content_sha256'],
      locator: identity['locator'],
      revision: identity['revision'],
      source: identity['source'],
    });
    const derived = `model_${
      createHash('sha256').update(canonicalCoordinates, 'ascii').digest('hex')
    }`;
    expect(derived).toBe(identity['model_id']);
  });

  it('keeps fixture schema markers aligned with the TypeScript contract', () => {
    const contracts = record(validFixture['contracts']);
    for (const [kind, schema] of Object.entries(
      MODEL_INTELLIGENCE_SCHEMAS,
    )) {
      expect(record(contracts[kind])['schema']).toBe(schema);
    }
  });

  it('preserves conditional capabilities and their stable reason code', () => {
    const payload = record(
      record(validFixture['contracts'])['capability_descriptor'],
    );
    const parsed = parseModelIntelligenceContract(
      'capability_descriptor',
      payload,
    );
    expect(parsed.state).toBe('conditional');
    expect(parsed.reason_code).toBe('requires_compatible_model_task');
  });
});
