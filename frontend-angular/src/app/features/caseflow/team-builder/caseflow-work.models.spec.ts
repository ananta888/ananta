/**
 * The two claims this view makes, and must not get wrong.
 *
 * "This agent is busy" and "this task is being created" are statements about
 * a running system. A status word we do not know must not be read as work in
 * progress, and a scope with nothing to filter by must not answer with the
 * whole organisation's work under one team's name.
 */

import { describe, expect, it } from 'vitest';
import {
  bucketCounts,
  narrowToScope,
  bucketLabel,
  bucketOf,
  byAssignee,
  inBucket,
  scopeIsAnswerable,
  scopeQuery,
  toTaskViews,
  toTraceEntries,
} from './caseflow-work.models';

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: 't-1',
    title: 'Index aktualisieren',
    status: 'in_progress',
    team_id: 'team-1',
    assigned_agent_url: 'http://agent-a:5001',
    task_kind: 'coding',
    created_at: 100,
    updated_at: 200,
    ...overrides,
  };
}

describe('which bucket a task belongs in', () => {
  it.each([
    ['in_progress', 'working'],
    ['assigned', 'working'],
    ['delegated', 'working'],
    ['created', 'emerging'],
    ['proposing', 'emerging'],
    ['todo', 'emerging'],
    ['reserved', 'emerging'],
    ['updated', 'emerging'],
    ['waiting_for_review', 'waiting'],
    ['blocked_by_dependency', 'waiting'],
    ['blocked', 'waiting'],
    ['paused', 'waiting'],
    ['completed', 'finished'],
    ['failed', 'finished'],
    ['cancelled', 'finished'],
  ])('reads %s as %s', (status, bucket) => {
    expect(bucketOf(status)).toBe(bucket);
  });

  it('does not claim an agent is busy on a status word it does not know', () => {
    for (const status of ['', '   ', 'brand_new_state', 'ARCHIVED']) {
      expect(bucketOf(status)).toBe('finished');
    }
  });

  it('reads a status regardless of how the Hub cased it', () => {
    expect(bucketOf('IN_PROGRESS')).toBe('working');
    expect(bucketOf(' Assigned ')).toBe('working');
  });

  it('names every bucket in words a person reads rather than status codes', () => {
    expect(bucketLabel('working')).toBe('Arbeitet gerade');
    expect(bucketLabel('emerging')).toBe('Entsteht gerade');
  });
});

describe('reading the task list', () => {
  it('accepts both the bare list and the enveloped one the Hub may send', () => {
    expect(toTaskViews([task()])).toHaveLength(1);
    expect(toTaskViews({ tasks: [task()] })).toHaveLength(1);
  });

  it('drops anything without an identity rather than rendering a nameless row', () => {
    expect(toTaskViews([task({ id: '' }), null, 'nonsense', task({ id: 't-2' })]).map(item => item.id)).toEqual(['t-2']);
  });

  it('falls back from a missing title to the description and then to the id', () => {
    expect(toTaskViews([task({ title: '', description: 'Beschreibung' })])[0].title).toBe('Beschreibung');
    expect(toTaskViews([task({ title: '', description: '' })])[0].title).toBe('t-1');
  });

  it('treats a missing timestamp as zero rather than as now', () => {
    expect(toTaskViews([task({ updated_at: undefined, created_at: 'x' })])[0].updated_at).toBe(0);
  });

  it('yields nothing at all for a payload of the wrong shape', () => {
    for (const payload of [null, undefined, 42, { other: [] }]) {
      expect(toTaskViews(payload)).toEqual([]);
    }
  });
});

describe('what is moving right now', () => {
  it('puts the most recently changed task first', () => {
    const tasks = toTaskViews([
      task({ id: 'old', updated_at: 10 }),
      task({ id: 'new', updated_at: 90 }),
      task({ id: 'mid', updated_at: 50 }),
    ]);

    expect(inBucket(tasks, 'working').map(item => item.id)).toEqual(['new', 'mid', 'old']);
  });

  it('counts every bucket, including the empty ones', () => {
    const tasks = toTaskViews([task(), task({ id: 't-2', status: 'created' })]);

    expect(bucketCounts(tasks)).toEqual({ working: 1, emerging: 1, waiting: 0, finished: 0 });
  });

  it('does not mutate the list it was given while sorting', () => {
    const tasks = toTaskViews([task({ id: 'a', updated_at: 1 }), task({ id: 'b', updated_at: 9 })]);

    inBucket(tasks, 'working');

    expect(tasks.map(item => item.id)).toEqual(['a', 'b']);
  });
});

describe('who is busy', () => {
  it('groups open work by the agent it went to', () => {
    const tasks = toTaskViews([
      task({ id: 'a', assigned_agent_url: 'http://a' }),
      task({ id: 'b', assigned_agent_url: 'http://b' }),
      task({ id: 'c', assigned_agent_url: 'http://a', status: 'todo' }),
    ]);

    const rows = byAssignee(tasks);

    expect(rows.map(row => row.agent)).toEqual(['http://a', 'http://b']);
    expect(rows[0].working.map(item => item.id)).toEqual(['a']);
    expect(rows[0].other.map(item => item.id)).toEqual(['c']);
  });

  it('keeps unassigned work visible under its own heading', () => {
    const rows = byAssignee(toTaskViews([task({ assigned_agent_url: '' })]));

    expect(rows).toHaveLength(1);
    expect(rows[0].agent).toBe('ohne Zuweisung');
  });

  it('leaves finished work out of who is busy', () => {
    const rows = byAssignee(toTaskViews([task({ status: 'completed' })]));

    expect(rows).toEqual([]);
  });

  it('puts whoever holds the most open work first', () => {
    const tasks = toTaskViews([
      task({ id: 'a', assigned_agent_url: 'http://quiet' }),
      task({ id: 'b', assigned_agent_url: 'http://busy' }),
      task({ id: 'c', assigned_agent_url: 'http://busy' }),
    ]);

    expect(byAssignee(tasks)[0].agent).toBe('http://busy');
  });
});

describe('the trace', () => {
  it('marks the events that brought a task into being', () => {
    const entries = toTraceEntries([
      { event_type: 'task_created', task_id: 't-1', actor: 'planner', timestamp: 5, details: { title: 'Neu' } },
      { event_type: 'task_assigned', task_id: 't-1', actor: 'hub', timestamp: 6 },
    ]);

    expect(entries.map(entry => entry.creating)).toEqual([true, false]);
    expect(entries[0].summary).toBe('Neu');
  });

  it('keeps the order the Hub sent rather than re-sorting by timestamp', () => {
    const entries = toTraceEntries([
      { event_type: 'a', task_id: 't', timestamp: 99 },
      { event_type: 'b', task_id: 't', timestamp: 1 },
    ]);

    expect(entries.map(entry => entry.event_type)).toEqual(['a', 'b']);
  });

  it('gives every line a key unique even when one task repeats', () => {
    const entries = toTraceEntries([
      { event_type: 'a', task_id: 't' },
      { event_type: 'b', task_id: 't' },
    ]);

    expect(new Set(entries.map(entry => entry.key)).size).toBe(2);
  });

  it('names the actor as the system rather than leaving a line unattributed', () => {
    expect(toTraceEntries([{ event_type: 'a', task_id: 't' }])[0].actor).toBe('System');
  });

  it('falls back to the event type when the details carry no readable text', () => {
    expect(toTraceEntries([{ event_type: 'task_failed', task_id: 't', details: { code: 7 } }])[0].summary)
      .toBe('task_failed');
  });

  it('yields nothing for a payload of the wrong shape', () => {
    for (const payload of [null, 'nope', { other: 1 }]) {
      expect(toTraceEntries(payload)).toEqual([]);
    }
  });
});

describe('scoping a level', () => {
  it('filters by the identity the level actually has', () => {
    expect(scopeQuery({ level: 'organization' })).toEqual({});
    expect(scopeQuery({ level: 'team', team_id: 'team-1' })).toEqual({ team_id: 'team-1' });
    expect(scopeQuery({ level: 'agent', agent: 'http://a' })).toEqual({ agent: 'http://a' });
  });

  it('refuses a level with nothing to filter by rather than answering too broadly', () => {
    expect(scopeIsAnswerable({ level: 'team' })).toBe(false);
    expect(scopeIsAnswerable({ level: 'agent', agent: '' })).toBe(false);
    expect(scopeIsAnswerable({ level: 'team', team_id: 'team-1' })).toBe(true);
    expect(scopeIsAnswerable({ level: 'organization' })).toBe(true);
  });

  it('never carries the filter of one level into another', () => {
    expect(scopeQuery({ level: 'organization', team_id: 'team-1', agent: 'http://a' })).toEqual({});
    expect(scopeQuery({ level: 'team', team_id: 'team-1', agent: 'http://a' })).toEqual({ team_id: 'team-1' });
  });
});


describe('the shape the Hub actually sends', () => {
  it('reads the timeline out of {items}, which is what /tasks/timeline returns', () => {
    const entries = toTraceEntries({ items: [{ event_type: 'task_created', task_id: 't-1' }], total: 1 });

    expect(entries).toHaveLength(1);
    expect(entries[0].creating).toBe(true);
  });

  it('still reads a bare list and an {events} envelope', () => {
    expect(toTraceEntries([{ event_type: 'a', task_id: 't' }])).toHaveLength(1);
    expect(toTraceEntries({ events: [{ event_type: 'a', task_id: 't' }] })).toHaveLength(1);
  });

  it('carries every level identity a task summary ships with', () => {
    const view = toTaskViews([
      task({ organization_id: 'org-1', unit_id: 'unit-1', role_slot_id: 'slot-1' }),
    ])[0];

    expect(view.organization_id).toBe('org-1');
    expect(view.unit_id).toBe('unit-1');
    expect(view.role_slot_id).toBe('slot-1');
  });
});

describe('narrowing to a level', () => {
  const tasks = () =>
    toTaskViews([
      task({ id: 'a', unit_id: 'unit-1', team_id: 'team-1', role_slot_id: 'slot-1' }),
      task({ id: 'b', unit_id: 'unit-2', team_id: 'team-2', role_slot_id: 'slot-2' }),
    ]);

  it.each([
    [{ level: 'unit', unit_id: 'unit-1' }, ['a']],
    [{ level: 'team', team_id: 'team-2' }, ['b']],
    [{ level: 'role_slot', role_slot_id: 'slot-1' }, ['a']],
  ])('keeps only what belongs to %o', (scope, expected) => {
    expect(narrowToScope(tasks(), scope as never).map(item => item.id)).toEqual(expected);
  });

  it('leaves an agent scope alone, because the Hub already filtered it', () => {
    expect(narrowToScope(tasks(), { level: 'agent', agent: 'http://a' })).toHaveLength(2);
  });

  it('leaves the whole organisation alone when it names no id', () => {
    expect(narrowToScope(tasks(), { level: 'organization' })).toHaveLength(2);
  });

  it('answers every level that carries its own identity', () => {
    expect(scopeIsAnswerable({ level: 'unit', unit_id: 'u' })).toBe(true);
    expect(scopeIsAnswerable({ level: 'unit' })).toBe(false);
    expect(scopeIsAnswerable({ level: 'role_slot', role_slot_id: 's' })).toBe(true);
    expect(scopeIsAnswerable({ level: 'role_slot' })).toBe(false);
  });
});
