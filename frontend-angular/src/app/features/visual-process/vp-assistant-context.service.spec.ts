import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';

import { canonicalVpJson, sha256VpCanonicalJson, VpAssistantContextService } from './vp-assistant-context.service';
import { VpGraph } from './visual-process-api.service';

interface GoldenVector {
  name: string;
  input: unknown;
  canonical_utf8: string;
  sha256: string;
}

let goldenVectors: GoldenVector[] = [];

beforeAll(async () => {
  const raw = await readFile(
    resolve(process.cwd(), '../tests/fixtures/visual_process/context_canonicalization.v1.json'),
    'utf8',
  );
  goldenVectors = (JSON.parse(raw) as { vectors: GoldenVector[] }).vectors;
});

const graph: VpGraph = {
  id: 'g', name: 'Graph', description: '', version: '1', tags: ['z', 'a'], edges: [],
  steps: [{ id: 's', label: 'Step', kind: 'task', gate: false, position: { x: 1, y: 2 }, io: { inputs: [], outputs: [] }, policy_hints: [], metadata: { api_key: 'secret', useful: true }, run_state: 'running' }],
};

describe('VpAssistantContextService', () => {
  it('matches all shared Python canonical bytes and SHA-256 vectors', async () => {
    expect(goldenVectors).toHaveLength(30);
    for (const vector of goldenVectors) {
      expect(canonicalVpJson(vector.input), vector.name).toBe(vector.canonical_utf8);
      await expect(sha256VpCanonicalJson(vector.input), vector.name).resolves.toBe(vector.sha256);
    }
  });

  it('rejects duplicate NFC keys and out-of-domain numbers', () => {
    expect(() => canonicalVpJson({ 'Caf\u00e9': 1, 'Cafe\u0301': 2 }))
      .toThrow('canonical_context_duplicate_normalized_key');
    expect(() => canonicalVpJson({ value: Number.POSITIVE_INFINITY }))
      .toThrow('canonical_context_number_non_finite');
    expect(() => canonicalVpJson({ value: 0.000_000_000_1 }))
      .toThrow('canonical_context_float_out_of_range');
  });

  it('creates the same context id for semantically equal stable input', async () => {
    const service = new VpAssistantContextService();
    const options = {
      graph,
      target: { kind: 'node' as const, entityId: 's', graphId: 'g', role: 'task', stepId: 's' },
      detailLevel: 'selected' as const,
      editorMode: 'full-editor' as const,
    };
    const first = await service.assemble(options);
    const second = await service.assemble({ ...options, graph: structuredClone(graph) });
    expect(first.context_id).toMatch(/^ctx-sha256:[a-f0-9]{64}$/);
    expect(second.context_id).toBe(first.context_id);
    expect(JSON.stringify(first.graph_excerpt)).not.toContain('secret');
    expect(JSON.stringify(first.graph_excerpt)).not.toContain('run_state');
    expect(first.editor_mode).toBe('editor');
    expect(first.repository_revision).toBe('unverified');
    expect(first.codecompass_manifest_hash).toBe('unverified');
    expect(first.source_allowlist_version).toBe('unverified');
    expect(first.prompt_version).toBe('visual-process-assistant.v1');
    expect(first.location).toMatchObject({ target_kind: 'node', entity_id: 's' });
  });

  it('omits undefined runtime fields while hashing the stable runtime snapshot', async () => {
    const service = new VpAssistantContextService();
    const result = await service.assemble({
      graph,
      target: { kind: 'node', entityId: 's', graphId: 'g', role: 'task', stepId: 's' },
      detailLevel: 'selected', editorMode: 'full-editor',
      runtime: {
        run_id: 'run', workflow_id: 'workflow', process_id: undefined, overall_status: 'running',
        current_step_ids: ['s'], steps: {}, updated_at: 123,
      },
    });
    expect(result.runtime_snapshot_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(Object.prototype.hasOwnProperty.call(result.runtime_overlay, 'process_id')).toBe(false);
  });
});
