import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, join, parse, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const FIXTURE_RELATIVE_PATH = join(
  'tests',
  'fixtures',
  'kanban_model_dashboard',
  'kanban-model-dashboard.v1.json',
);

type JsonRecord = Record<string, unknown>;

function findRepositoryRoot(startPath: string): string {
  let candidate = resolve(startPath);
  const filesystemRoot = parse(candidate).root;

  while (true) {
    if (
      existsSync(join(candidate, 'frontend-angular', 'package.json'))
      && existsSync(join(candidate, FIXTURE_RELATIVE_PATH))
    ) {
      return candidate;
    }
    if (candidate === filesystemRoot) {
      throw new Error(`Repository fixture not found: ${FIXTURE_RELATIVE_PATH}`);
    }
    candidate = dirname(candidate);
  }
}

function record(value: unknown, label: string): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value as JsonRecord;
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (!Number.isInteger(value) || Number(value) < 0) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  return Number(value);
}

const repositoryRoot = findRepositoryRoot(dirname(fileURLToPath(import.meta.url)));
const fixturePath = join(repositoryRoot, FIXTURE_RELATIVE_PATH);
const fixture = record(
  JSON.parse(readFileSync(fixturePath, 'utf8')) as unknown,
  'fixture',
);

describe('shared Kanban and model dashboard fixture contract', () => {
  it('reads the canonical repository fixture directly', () => {
    expect(realpathSync(fixturePath)).toBe(
      realpathSync(join(repositoryRoot, FIXTURE_RELATIVE_PATH)),
    );
    expect(fixture['fixture_version']).toBe('kanban-model-dashboard.fixture.v1');
    expect(record(fixture['_meta'], '_meta')['deterministic']).toBe(true);
  });

  it('validates the board and card revisions and their identity relationship', () => {
    const board = record(fixture['board'], 'board');
    const card = record(fixture['card'], 'card');

    const boardId = nonEmptyString(board['id'], 'board.id');
    const boardRevision = nonEmptyString(board['revision'], 'board.revision');
    const cardRevision = nonNegativeInteger(card['revision'], 'card.revision');

    expect(board['schema_version']).toBe('kanban.v1');
    expect(boardRevision).toMatch(/^[A-Za-z0-9._:-]+$/);
    expect(board['scope_type']).toBe('hub');
    expect(board['card_count']).toBeGreaterThanOrEqual(1);
    expect(Array.isArray(board['capabilities'])).toBe(true);

    expect(card['schema_version']).toBe('kanban.v1');
    expect(card['board_id']).toBe(boardId);
    expect(nonEmptyString(card['id'], 'card.id')).not.toBe(boardId);
    expect(cardRevision).toBeGreaterThanOrEqual(1);
  });

  it('validates event sequencing against the card projection', () => {
    const board = record(fixture['board'], 'board');
    const card = record(fixture['card'], 'card');
    const event = record(fixture['event'], 'event');

    expect(event['schema_version']).toBe('kanban.event.v1');
    expect(nonEmptyString(event['event_id'], 'event.event_id')).toBeTruthy();
    expect(event['board_id']).toBe(board['id']);
    expect(event['task_id']).toBe(card['id']);
    expect(nonNegativeInteger(event['revision'], 'event.revision')).toBe(card['revision']);
    expect(nonNegativeInteger(event['sequence'], 'event.sequence')).toBeGreaterThanOrEqual(1);
    expect(nonEmptyString(event['event_type'], 'event.event_type')).toMatch(
      /^kanban\.[a-z0-9_.-]+$/,
    );
  });

  it('keeps the revision-conflict response stable at HTTP 409', () => {
    const error = record(fixture['error'], 'error');
    const body = record(error['body'], 'error.body');
    const conflict = record(body['error'], 'error.body.error');
    const details = record(conflict['details'], 'error.body.error.details');

    expect(error['http_status']).toBe(409);
    expect(conflict['code']).toBe('kanban_revision_conflict');
    expect(conflict['message']).toBe('kanban_revision_conflict');
    expect(nonNegativeInteger(
      details['current_revision'],
      'error.body.error.details.current_revision',
    )).toBeGreaterThan(0);
  });

  it('validates the complete model summary wire shape', () => {
    const model = record(fixture['model_summary'], 'model_summary');

    expect(model['schema']).toBe('ananta.model-summary.v1');
    expect(nonEmptyString(model['provider_id'], 'model_summary.provider_id')).toBeTruthy();
    expect(nonEmptyString(model['model_id'], 'model_summary.model_id')).toBeTruthy();
    expect(nonEmptyString(model['display_name'], 'model_summary.display_name')).toBeTruthy();
    expect(['local', 'cloud', 'remote', 'voice', 'unknown']).toContain(model['runtime']);
    expect(['available', 'degraded', 'unavailable', 'unknown']).toContain(
      model['availability'],
    );
    expect(['healthy', 'degraded', 'unavailable', 'unknown']).toContain(model['health']);
    expect([true, false, null]).toContain(model['loaded']);
    expect([true, false]).toContain(model['is_default']);
    expect(Array.isArray(model['capabilities'])).toBe(true);
  });
});
