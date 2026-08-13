import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { VpGraph } from './visual-process-api.service';
import { VpEditorStateFacade } from './vp-editor-state.facade';

interface GraphRoundtripVector {
  name: string;
  input: Record<string, unknown>;
  preserve_paths: string[];
}

function jsonPointer(payload: unknown, pointer: string): unknown {
  return pointer.slice(1).split('/').reduce<unknown>((current, rawPart) => {
    const part = rawPart.replaceAll('~1', '/').replaceAll('~0', '~');
    return Array.isArray(current)
      ? current[Number(part)]
      : (current as Record<string, unknown>)[part];
  }, payload);
}

function graph(): VpGraph {
  return {
    id: 'a', name: 'A', description: '', version: '1', tags: [], edges: [],
    steps: [{
      id: 'step', label: 'Step', kind: 'task', gate: false, policy_hints: [],
      position: { x: 0, y: 0 }, io: { inputs: [], outputs: [] },
    }],
  };
}

describe('VpEditorStateFacade', () => {
  it('roundtrips the eight shared legacy, current and additive graph vectors', () => {
    const fixturePath = resolve(process.cwd(), '../tests/fixtures/visual_process/graph_roundtrip.v1.json');
    const fixture = JSON.parse(readFileSync(fixturePath, 'utf8')) as {
      schema: string;
      vectors: GraphRoundtripVector[];
    };
    expect(fixture.schema).toBe('ananta.visual_process.graph_roundtrip_vectors.v1');
    expect(fixture.vectors.length).toBeGreaterThanOrEqual(8);

    for (const vector of fixture.vectors) {
      const state = new VpEditorStateFacade();
      state.initialize(vector.input as unknown as VpGraph);
      const roundtripped = structuredClone(state.graph()) as unknown as Record<string, unknown>;
      for (const pointer of vector.preserve_paths) {
        expect(jsonPointer(roundtripped, pointer), `${vector.name}:${pointer}`).toEqual(
          jsonPointer(vector.input, pointer),
        );
      }
    }
  });

  it('keeps parallel embedded editor state isolated', () => {
    const a = new VpEditorStateFacade();
    const b = new VpEditorStateFacade();
    const source = graph();
    a.initialize(source);
    a.selectedId.set('step');
    a.mutate('rename', draft => { draft.name = 'Changed'; });
    expect(b.selectedId()).toBeNull();
    expect(b.dirty()).toBe(false);
    expect(a.graph()).not.toBe(source);
    a.destroy();
    expect(a.selectedId()).toBeNull();
  });

  it('keeps dirty-state, validation and undo/redo consistent', () => {
    const state = new VpEditorStateFacade();
    state.initialize(graph());
    state.validation.set({ valid: true, error_count: 0, warning_count: 0, issues: [] });

    state.mutate('rename', draft => { draft.name = 'Changed'; });

    expect(state.dirty()).toBe(true);
    expect(state.validation()).toBeNull();
    expect(state.canUndo()).toBe(true);
    expect(state.undo()).toBe(true);
    expect(state.graph().name).toBe('A');
    expect(state.dirty()).toBe(false);
    expect(state.redo()).toBe(true);
    expect(state.graph().name).toBe('Changed');
  });

  it('coalesces all drag updates into one transaction', () => {
    const state = new VpEditorStateFacade();
    state.initialize(graph());
    state.beginTransaction('move step');
    state.mutate('move', draft => { draft.steps[0].position.x = 10; }, { recordHistory: false });
    state.mutate('move', draft => { draft.steps[0].position.x = 30; }, { recordHistory: false });
    expect(state.commitTransaction()).toBe(true);

    expect(state.graph().steps[0].position.x).toBe(30);
    expect(state.undo()).toBe(true);
    expect(state.graph().steps[0].position.x).toBe(0);
    expect(state.canUndo()).toBe(false);
  });

  it('coalesces repeated field edits and tracks the saved baseline', () => {
    const state = new VpEditorStateFacade();
    state.initialize(graph());
    state.mutate('name', draft => { draft.name = 'AB'; }, { coalesceKey: 'graph:name' });
    state.mutate('name', draft => { draft.name = 'ABC'; }, { coalesceKey: 'graph:name' });
    state.markSaved();
    expect(state.dirty()).toBe(false);
    state.mutate('description', draft => { draft.description = 'x'; });
    expect(state.dirty()).toBe(true);
    state.undo();
    expect(state.dirty()).toBe(false);
    state.undo();
    expect(state.graph().name).toBe('A');
  });

  it('rolls back a failed command without changing revision, dirty state or validation', () => {
    const state = new VpEditorStateFacade();
    state.initialize(graph());
    const validation = { valid: true, error_count: 0, warning_count: 0, issues: [] };
    state.validation.set(validation);
    const revision = state.revision();

    expect(() => state.dispatch({
      label: 'broken',
      apply: draft => {
        draft.name = 'Must not leak';
        throw new Error('failed-command');
      },
    })).toThrow('failed-command');

    expect(state.graph().name).toBe('A');
    expect(state.revision()).toBe(revision);
    expect(state.dirty()).toBe(false);
    expect(state.validation()).toBe(validation);
    expect(state.canUndo()).toBe(false);
  });

  it('keeps only the configured number of complete transactions', () => {
    const state = new VpEditorStateFacade(2);
    state.initialize(graph());
    state.mutate('one', draft => { draft.name = 'one'; });
    state.mutate('two', draft => { draft.name = 'two'; });
    state.mutate('three', draft => { draft.name = 'three'; });

    expect(state.undo()).toBe(true);
    expect(state.graph().name).toBe('two');
    expect(state.undo()).toBe(true);
    expect(state.graph().name).toBe('one');
    expect(state.undo()).toBe(false);
  });

  it('keeps the latest Hub revision and hash across undo/redo after save', () => {
    const state = new VpEditorStateFacade();
    state.initialize({ ...graph(), definition_revision: 2, base_graph_hash: 'a'.repeat(64) });
    state.mutate('rename', draft => { draft.name = 'Saved name'; });
    const request = state.captureSaveRequest();
    state.acceptSaveResult({
      id: 'a', version: '2', graph_schema_version: '1', node_registry_version: 'registry-1',
      definition_revision: 3, base_graph_hash: 'b'.repeat(64), saved: true,
    }, request);

    expect(state.undo()).toBe(true);
    expect(state.graph()).toMatchObject({ name: 'A', version: '2', definition_revision: 3, base_graph_hash: 'b'.repeat(64) });
    expect(state.dirty()).toBe(true);
    expect(state.redo()).toBe(true);
    expect(state.graph()).toMatchObject({ name: 'Saved name', version: '2', definition_revision: 3, base_graph_hash: 'b'.repeat(64) });
    expect(state.dirty()).toBe(false);
  });

  it('keeps edits made during an asynchronous save dirty and adopts only persistence identity', () => {
    const state = new VpEditorStateFacade();
    state.initialize({ ...graph(), definition_revision: 2, base_graph_hash: 'a'.repeat(64) });
    state.mutate('submitted edit', draft => { draft.name = 'Submitted'; });
    const request = state.captureSaveRequest();

    state.mutate('later edit', draft => { draft.description = 'Not submitted'; });
    const acceptance = state.acceptSaveResult({
      id: 'a', version: '2', graph_schema_version: '1', node_registry_version: 'registry-1',
      definition_revision: 3, base_graph_hash: 'b'.repeat(64), saved: true,
    }, request);

    expect(acceptance).toEqual({ status: 'accepted_dirty', request_id: request.request_id });
    expect(state.graph()).toMatchObject({
      name: 'Submitted',
      description: 'Not submitted',
      definition_revision: 3,
      base_graph_hash: 'b'.repeat(64),
    });
    expect(state.dirty()).toBe(true);
  });

  it('rejects a save response for a different graph without mutating the draft', () => {
    const state = new VpEditorStateFacade();
    state.initialize(graph());
    state.mutate('rename', draft => { draft.name = 'Local'; });
    const request = state.captureSaveRequest();
    const before = structuredClone(state.graph());

    expect(state.acceptSaveResult({
      id: 'different', version: '2', definition_revision: 3,
      base_graph_hash: 'b'.repeat(64), saved: true,
    }, request)).toEqual({ status: 'rejected_identity', request_id: request.request_id });
    expect(state.graph()).toEqual(before);
    expect(state.dirty()).toBe(true);
  });

  it('rejects an older overlapping response after a newer request was issued', () => {
    const state = new VpEditorStateFacade();
    state.initialize({ ...graph(), definition_revision: 2, base_graph_hash: 'a'.repeat(64) });
    state.mutate('first', draft => { draft.name = 'First'; });
    const older = state.captureSaveRequest();
    state.mutate('second', draft => { draft.name = 'Second'; });
    const newer = state.captureSaveRequest();

    expect(state.acceptSaveResult({
      id: 'a', version: '4', definition_revision: 4,
      base_graph_hash: 'd'.repeat(64), saved: true,
    }, newer)).toEqual({ status: 'accepted_clean', request_id: newer.request_id });
    const acceptedGraph = structuredClone(state.graph());

    expect(state.acceptSaveResult({
      id: 'a', version: '3', definition_revision: 3,
      base_graph_hash: 'c'.repeat(64), saved: true,
    }, older)).toEqual({ status: 'rejected_stale', request_id: older.request_id });
    expect(state.graph()).toEqual(acceptedGraph);
    expect(state.graph()).toMatchObject({
      name: 'Second', definition_revision: 4, base_graph_hash: 'd'.repeat(64),
    });
  });

  it('rejects a save response captured before a same-id graph reload', () => {
    const state = new VpEditorStateFacade();
    state.initialize({ ...graph(), definition_revision: 2, base_graph_hash: 'a'.repeat(64) });
    state.mutate('submitted', draft => { draft.name = 'Submitted'; });
    const request = state.captureSaveRequest();
    state.initialize({
      ...graph(), name: 'Reloaded', definition_revision: 9, base_graph_hash: 'z'.repeat(64),
    });

    expect(state.acceptSaveResult({
      id: 'a', version: '3', definition_revision: 3,
      base_graph_hash: 'b'.repeat(64), saved: true,
    }, request)).toEqual({ status: 'rejected_stale', request_id: request.request_id });
    expect(state.graph()).toMatchObject({
      id: 'a', name: 'Reloaded', definition_revision: 9, base_graph_hash: 'z'.repeat(64),
    });
    expect(state.dirty()).toBe(false);
  });
});
