import { describe, expect, it } from 'vitest';
import type { VpGraph } from '../../visual-process/visual-process-api.service';
import { setAgentRoleAndIcon } from './caseflow-agent-graph.commands';
import {
  CASEFLOW_AGENT_CANVAS_EXTENSION,
  CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
} from './caseflow-agent-canvas.models';
import {
  CASEFLOW_AGENT_ICON_ALLOWLIST,
  CASEFLOW_AGENT_ROLE_CATALOG,
  CASEFLOW_AGENT_ROLES_BY_CATEGORY,
  CaseFlowAgentIcon,
  validateRoleSelection,
} from './caseflow-role-catalog';

describe('CaseFlow agent role and icon catalog v1', () => {
  it('contains every declared role exactly once in the expected category', () => {
    expect(CASEFLOW_AGENT_ROLES_BY_CATEGORY.software.map(role => role.id)).toEqual([
      'architect', 'developer', 'frontend', 'backend', 'devops', 'tester',
      'security', 'reviewer', 'product_owner', 'scrum_master',
    ]);
    expect(CASEFLOW_AGENT_ROLES_BY_CATEGORY.creative.map(role => role.id)).toEqual([
      'artist', 'designer', 'ux', 'writer', 'video', 'audio',
    ]);
    expect(CASEFLOW_AGENT_ROLES_BY_CATEGORY.business.map(role => role.id)).toEqual([
      'marketing', 'sales', 'finance', 'legal', 'hr', 'business_analyst',
      'researcher', 'project_manager',
    ]);
    expect(CASEFLOW_AGENT_ROLES_BY_CATEGORY.generic.map(role => role.id)).toEqual([
      'lead', 'specialist', 'critic', 'approver', 'observer', 'custom',
    ]);

    const ids = CASEFLOW_AGENT_ROLE_CATALOG.map(role => role.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(CASEFLOW_AGENT_ROLE_CATALOG.every(role =>
      CASEFLOW_AGENT_ICON_ALLOWLIST.includes(role.default_icon))).toBe(true);
  });

  it('accepts a named custom role only with an allowlisted icon', () => {
    const valid = validateRoleSelection({
      role_id: 'custom',
      custom_name: 'Evidence Curator',
      icon: 'science',
    });
    expect(valid.ok).toBe(true);
    if (valid.ok) {
      expect(valid.value).toEqual({
        canonical_role: 'Evidence Curator',
        role_preset: 'custom',
        icon: 'science',
      });
    }

    expect(validateRoleSelection({
      role_id: 'custom',
      custom_name: '   ',
      icon: 'science',
    }).ok).toBe(false);
    expect(validateRoleSelection({
      role_id: 'custom',
      custom_name: 'Evidence Curator',
      icon: 'remote_injected_icon' as CaseFlowAgentIcon,
    }).ok).toBe(false);
  });

  it('changes role and icon without changing skills, routing, tools, policies or unknown fields', () => {
    const graph = fixtureGraph();
    const stepBefore = graph.steps[0];
    const result = setAgentRoleAndIcon(graph, 'agent', {
      role_id: 'security',
      icon: 'security',
    });
    if (!result.ok) throw new Error(JSON.stringify(result.issues));
    const stepAfter = result.value.steps[0];
    const extension = result.value.extensions?.[CASEFLOW_AGENT_CANVAS_EXTENSION] as Record<string, any>;

    expect(result.value).not.toBe(graph);
    expect(stepAfter).not.toBe(stepBefore);
    expect(stepAfter.role).toBe('security');
    expect(extension['nodes']['agent']['icon']).toBe('security');
    expect(stepAfter.agent_skill_profile_id).toBe(stepBefore.agent_skill_profile_id);
    expect(stepAfter.metadata?.['model_routing']).toEqual(stepBefore.metadata?.['model_routing']);
    expect(stepAfter.metadata?.['allowed_tools']).toEqual(stepBefore.metadata?.['allowed_tools']);
    expect(stepAfter.metadata?.['capabilities']).toEqual(stepBefore.metadata?.['capabilities']);
    expect(stepAfter.policy_hints).toBe(stepBefore.policy_hints);
    expect(stepAfter.gate).toBe(stepBefore.gate);
    expect((stepAfter as any).future_step).toEqual((stepBefore as any).future_step);
    expect(extension['nodes']['agent']['future_presentation']).toEqual({ keep: true });
  });

  it('fails closed and preserves graph identity for a non-allowlisted icon', () => {
    const graph = fixtureGraph();
    const result = setAgentRoleAndIcon(graph, 'agent', {
      role_id: 'developer',
      icon: 'javascript:alert(1)' as CaseFlowAgentIcon,
    });

    expect(result.ok).toBe(false);
    expect(result.issues[0]?.code).toBe('agent_icon_not_allowed');
    expect(graph.steps[0].role).toBe('developer');
  });
});

function fixtureGraph(): VpGraph {
  return {
    id: 'roles',
    name: 'Roles',
    description: '',
    version: '1',
    tags: [],
    edges: [],
    steps: [{
      id: 'agent',
      label: 'Agent',
      kind: 'coding',
      role: 'developer',
      agent_skill_profile_id: 'coder',
      io: { inputs: [], outputs: [] },
      position: { x: 0, y: 0 },
      policy_hints: ['requires_approval'],
      gate: true,
      metadata: {
        model_routing: { preferred_profile_id: 'local-coder', strategy: 'per_step' },
        allowed_tools: ['read_file'],
        capabilities: ['read_only'],
      },
      future_step: { preserved: true },
    } as any],
    extensions: {
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: {
        schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
        nodes: {
          agent: {
            icon: 'code',
            future_presentation: { keep: true },
          },
        },
      },
    },
  };
}
