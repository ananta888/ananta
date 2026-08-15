/**
 * What a template turns into, and what a person is not allowed to save.
 *
 * The point of this view is that a team stays readable while it runs, so the
 * cases pinned here are the ones that would make a running team unreadable:
 * two agents with the same name, an agent with none, a graph the existing
 * editors would not recognise.
 */

import { describe, expect, it } from 'vitest';
import {
  CASEFLOW_AGENT_CANVAS_EXTENSION,
  CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
  type TeamTemplate,
  buildGraph,
  draftFromTemplate,
  isSmallTeam,
  validateDraft,
  withAddedAgent,
  withRemovedAgent,
  withRenamedAgent,
} from './caseflow-team-builder.models';

function template(overrides: Partial<TeamTemplate> = {}): TeamTemplate {
  return {
    template_id: 'team:tt-1',
    kind: 'team',
    source_id: 'tt-1',
    display_name: 'Scrum',
    description: 'Ein Standardteam',
    roles: [
      { role_id: 'r-1', display_name: 'Product Owner' },
      { role_id: 'r-2', display_name: 'Scrum Master' },
      { role_id: 'r-3', display_name: 'Developer' },
    ],
    aliases: [],
    agent_count: 3,
    ...overrides,
  };
}

describe('starting from a template', () => {
  it('gives every role an agent, named after the role until someone renames it', () => {
    const draft = draftFromTemplate(template());

    expect(draft.agents.map(agent => agent.name)).toEqual(['Product Owner', 'Scrum Master', 'Developer']);
    expect(draft.agents.map(agent => agent.role_id)).toEqual(['r-1', 'r-2', 'r-3']);
    expect(draft.team_name).toBe('Scrum');
  });

  it('keeps a name the person already typed rather than overwriting it', () => {
    expect(draftFromTemplate(template(), 'Team Blau').team_name).toBe('Team Blau');
  });

  it('starts an agentless process template empty rather than refusing it', () => {
    const draft = draftFromTemplate(template({ roles: [], agent_count: 0, kind: 'process' }));

    expect(draft.agents).toEqual([]);
    expect(validateDraft(draft).map(issue => issue.code)).toContain('team_needs_an_agent');
  });
});

describe('editing the draft', () => {
  it('names an added agent apart from the one already holding that name', () => {
    const draft = withAddedAgent(draftFromTemplate(template()), { role_id: 'r-3', display_name: 'Developer' });

    expect(draft.agents.at(-1)?.name).toBe('Developer 2');
    expect(validateDraft(draft)).toEqual([]);
  });

  it('does not reuse the step id of an agent that is still present', () => {
    const removed = withRemovedAgent(draftFromTemplate(template()), 'agent-2');
    const added = withAddedAgent(removed, { role_id: 'r-4', display_name: 'Tester' });

    const ids = added.agents.map(agent => agent.step_id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('renames only the agent that was asked for', () => {
    const draft = withRenamedAgent(draftFromTemplate(template()), 'agent-2', 'Mara');

    expect(draft.agents.map(agent => agent.name)).toEqual(['Product Owner', 'Mara', 'Developer']);
  });
});

describe('what may not be saved', () => {
  it('refuses two agents a person could not tell apart while they run', () => {
    const draft = withRenamedAgent(draftFromTemplate(template()), 'agent-2', 'product owner');

    const issues = validateDraft(draft);
    expect(issues.map(issue => issue.code)).toEqual(['agent_names_must_differ']);
    expect(issues[0].step_id).toBe('agent-2');
  });

  it('refuses a blank or whitespace-only agent name', () => {
    const draft = withRenamedAgent(draftFromTemplate(template()), 'agent-1', '   ');

    expect(validateDraft(draft).map(issue => issue.code)).toEqual(['agent_name_required']);
  });

  it('refuses a team without a name', () => {
    const draft = { ...draftFromTemplate(template()), team_name: '  ' };

    expect(validateDraft(draft).map(issue => issue.code)).toEqual(['team_name_required']);
  });

  it('refuses a team name too long to render anywhere it is shown', () => {
    const draft = { ...draftFromTemplate(template()), team_name: 'x'.repeat(121) };

    expect(validateDraft(draft).map(issue => issue.code)).toEqual(['team_name_too_long']);
  });

  it('accepts a draft whose names differ', () => {
    expect(validateDraft(draftFromTemplate(template()))).toEqual([]);
  });
});

describe('the graph a draft saves as', () => {
  it('carries each agent as a step labelled with the name a person gave it', () => {
    const draft = withRenamedAgent(draftFromTemplate(template()), 'agent-1', ' Mara ');

    const graph = buildGraph(draft, 'graph-1') as { steps: Array<Record<string, unknown>> };

    expect(graph.steps).toHaveLength(3);
    expect(graph.steps[0].label).toBe('Mara');
    expect(graph.steps[0].role).toBe('Product Owner');
    expect(graph.steps[0].metadata).toEqual({ role_id: 'r-1' });
  });

  it('chains the agents in the order they were arranged', () => {
    const graph = buildGraph(draftFromTemplate(template()), 'graph-1') as {
      edges: Array<{ source: string; target: string }>;
    };

    expect(graph.edges.map(edge => [edge.source, edge.target])).toEqual([
      ['agent-1', 'agent-2'],
      ['agent-2', 'agent-3'],
    ]);
  });

  it('lays the agents out left to right so the first one reads first', () => {
    const graph = buildGraph(draftFromTemplate(template()), 'graph-1') as {
      steps: Array<{ position: { x: number; y: number } }>;
    };

    const xs = graph.steps.map(step => step.position.x);
    expect(xs).toEqual([...xs].sort((left, right) => left - right));
    expect(new Set(xs).size).toBe(xs.length);
  });

  it('declares the canvas extension so the agent view recognises what it opens', () => {
    const graph = buildGraph(draftFromTemplate(template()), 'graph-1');

    const extension = graph.extensions[CASEFLOW_AGENT_CANVAS_EXTENSION] as {
      schema: string;
      nodes: Record<string, unknown>;
    };
    expect(extension.schema).toBe(CASEFLOW_AGENT_CANVAS_SCHEMA_V1);
    expect(Object.keys(extension.nodes)).toEqual(['agent-1', 'agent-2', 'agent-3']);
  });

  it('produces a single agent without an edge rather than a dangling one', () => {
    const draft = withRemovedAgent(withRemovedAgent(draftFromTemplate(template()), 'agent-2'), 'agent-3');

    const graph = buildGraph(draft, 'graph-1');

    expect(graph.steps).toHaveLength(1);
    expect(graph.edges).toEqual([]);
  });
});

describe('which templates suit a small team', () => {
  it.each([
    [2, false],
    [3, true],
    [10, true],
    [11, false],
  ])('a %i-agent template counts as small: %s', (count, expected) => {
    expect(isSmallTeam(template({ agent_count: count }))).toBe(expected);
  });
});
