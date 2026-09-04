import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, parse, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CollaborationContractContext,
  CollaborationContractGateError,
  CollaborationContractKind,
  canonicalJson,
  validateCollaborationContract,
} from './collaboration-contract-gate';

type JsonRecord = Record<string, unknown>;

function repositoryRoot(startPath: string): string {
  let candidate = resolve(startPath);
  const root = parse(candidate).root;
  while (candidate !== root) {
    if (existsSync(join(candidate, 'tests', 'fixtures', 'scenarios', 'collaboration-contract-parity.v1.json'))) {
      return candidate;
    }
    candidate = dirname(candidate);
  }
  throw new Error('collaboration_contract_fixture_root_not_found');
}

function record(value: unknown): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('fixture_object_required');
  return value as JsonRecord;
}

function clone(value: JsonRecord): JsonRecord {
  return structuredClone(value);
}

function mutate(source: JsonRecord, mutationValue: unknown): JsonRecord {
  const mutation = record(mutationValue);
  const result = clone(source);
  if (mutation['operation'] === 'merge') {
    Object.assign(result, record(mutation['value']));
    return result;
  }
  const parts = String(mutation['path']).split('.');
  let target = result;
  for (const part of parts.slice(0, -1)) target = record(target[part]);
  let value = mutation['value'];
  if (mutation['operation'] === 'repeat') value = String(value).repeat(Number(mutation['count']));
  target[parts.at(-1)!] = value;
  return result;
}

async function sha256(value: unknown): Promise<string> {
  return createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
}

const root = repositoryRoot(dirname(fileURLToPath(import.meta.url)));
const fixture = record(JSON.parse(readFileSync(
  join(root, 'tests', 'fixtures', 'scenarios', 'collaboration-contract-parity.v1.json'),
  'utf8',
)));
const contracts = record(fixture['contracts']);

describe('collaboration Python/TypeScript contract parity', () => {
  it('accepts every shared positive contract without network access', async () => {
    for (const [kind, payload] of Object.entries(contracts)) {
      await expect(validateCollaborationContract(
        kind as CollaborationContractKind,
        payload,
        {},
        sha256,
      )).resolves.toEqual(payload);
    }
  });

  it('rejects the shared negative matrix with stable reason codes', async () => {
    for (const caseValue of fixture['negative_cases'] as unknown[]) {
      const testCase = record(caseValue);
      const payload = mutate(record(contracts[String(testCase['contract'])]), testCase['mutation']);
      try {
        await validateCollaborationContract(
          testCase['contract'] as CollaborationContractKind,
          payload,
          record(testCase['context']) as CollaborationContractContext,
          sha256,
        );
        throw new Error('negative_fixture_was_accepted');
      } catch (error) {
        expect(error).toBeInstanceOf(CollaborationContractGateError);
        expect((error as CollaborationContractGateError).reasonCode).toBe(testCase['expected_reason_code']);
      }
    }
  });

  it('keeps the fixture content-free and deterministic', () => {
    expect(fixture['schema']).toBe('ananta.collaboration-contract-parity-fixture.v1');
    expect(JSON.stringify(fixture)).not.toMatch(/Bearer |password|private_key/);
  });
});
