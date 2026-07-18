import { describe, expect, it } from 'vitest';

import { FALLBACK_KINDS } from './vp-editor-config';
import { VP_NODE_REGISTRY_VERSION } from './vp-node-definition-registry.service';
import {
  GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS,
  GENERATED_VISUAL_PROCESS_NODE_REGISTRY_VERSION,
  GENERATED_VISUAL_PROCESS_TASK_KINDS,
} from './vp-node-definitions.generated';

const CANONICAL_KIND_IDS = [
  'approval', 'codecompass_fts_search', 'codecompass_graph_expand', 'codecompass_index_build',
  'codecompass_vector_search', 'command_execute', 'domain_cluster', 'embed_api', 'embed_chunk',
  'evolution_analyze', 'evolution_apply', 'evolution_validate', 'evolve_project', 'evolve_prompt',
  'file_check', 'fork', 'git_op', 'join', 'ml_intern_build_lora_dataset', 'ml_intern_train_lora',
  'patch_apply', 'patch_propose', 'plan_only', 'query_rewrite', 'rag_retrieve', 'regex_check',
  'rerank', 'research_limited', 'review', 'run_tests', 'script', 'shell_execute', 'sign_rotation',
  'summarize', 'turboquant_mse', 'workspace_diff', 'workspace_snapshot',
];

describe('Visual Process fallback task-kind registry', () => {
  it('contains every canonical kind exactly once and no legacy alias', () => {
    const actual = FALLBACK_KINDS.map(item => item.id).sort();
    expect(actual).toEqual(CANONICAL_KIND_IDS);
    expect(new Set(actual).size).toBe(37);
    expect(actual).not.toContain('shell_execution');
    expect(actual).not.toContain('parallel');
    expect(actual).not.toContain('cluster');
    expect(VP_NODE_REGISTRY_VERSION).toBe('1.0.0');
    expect(VP_NODE_REGISTRY_VERSION).toBe(GENERATED_VISUAL_PROCESS_NODE_REGISTRY_VERSION);
    expect(FALLBACK_KINDS).toEqual(GENERATED_VISUAL_PROCESS_TASK_KINDS);
    expect(new Set(GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS.map(item => item.kind))).toEqual(new Set(actual));
    for (const definition of GENERATED_VISUAL_PROCESS_NODE_DEFINITIONS) {
      const fallback = FALLBACK_KINDS.find(item => item.id === definition.kind)!;
      expect(fallback.implementation_state, definition.kind).toBe(definition.runtime['implementation_state']);
      expect(fallback.dispatch_capable, definition.kind).toBe(definition.runtime['dispatch_capable']);
      expect(fallback.side_effects, definition.kind).toEqual(definition.runtime['side_effects']);
      expect(fallback.requires_approval, definition.kind).toBe(definition.runtime['requires_approval']);
    }
  });
});
