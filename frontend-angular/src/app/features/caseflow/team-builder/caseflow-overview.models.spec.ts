/**
 * Two subsystems, one list.
 *
 * Organisations and agent teams describe the same thing at different sizes,
 * so what is pinned here is that both become the same card, that a node the
 * Hub sends with no readable name still renders as something a person can
 * point at, and that the Hub's own shape is kept rather than recomputed.
 */

import { describe, expect, it } from 'vitest';
import type {
  OrganizationSummary,
  OrganizationTopologyPage,
} from '../../organizations/models/organization-topology.models';
import type { SavedGraphSummary } from '../../visual-process/visual-process-api.service';
import {
  childrenOf,
  countByLevel,
  descendantsOf,
  matchesSearch,
  organizationCard,
  structureNodes,
  teamCard,
  workScopeFor,
} from './caseflow-overview.models';

function organization(overrides: Partial<OrganizationSummary> = {}): OrganizationSummary {
  return {
    id: 'org-1',
    key: 'produkt',
    title: 'Produktorganisation',
    lifecycle: 'draft',
    definition_revision: 'r1',
    snapshot_hash: 'h',
    team_count: 4,
    unit_count: 2,
    lock_version: 1,
    revision: 'r1',
    ...overrides,
  } as OrganizationSummary;
}

function team(overrides: Partial<SavedGraphSummary> = {}): SavedGraphSummary {
  return {
    id: 'g-1',
    name: 'Mein Team',
    description: 'Drei Agenten',
    tags: [],
    updated_at: 0,
    created_at: 0,
    ...overrides,
  } as SavedGraphSummary;
}

function page(nodes: readonly Record<string, unknown>[], overlay: unknown = null): OrganizationTopologyPage {
  return {
    organization_id: 'org-1',
    definition_revision: 'r1',
    snapshot_hash: 'h',
    nodes,
    edges: [],
    runtime_overlay: overlay,
    diagnostics: [],
    limits: {},
    next_cursor: null,
    truncated: false,
  } as unknown as OrganizationTopologyPage;
}

function node(overrides: Record<string, unknown> = {}) {
  return {
    id: 'n-1',
    stable_key: 'team.alpha',
    kind: 'team',
    label: 'Alpha',
    depth: 1,
    child_count: 0,
    parent_id: 'org-1',
    ...overrides,
  };
}

describe('cards', () => {
  it('describes an organisation by what it holds, not by its revision', () => {
    const card = organizationCard(organization());

    expect(card.level).toBe('organization');
    expect(card.title).toBe('Produktorganisation');
    expect(card.subtitle).toContain('4 Teams');
    expect(card.subtitle).toContain('2 Bereiche');
    expect(card.subtitle).toContain('Entwurf');
  });

  it('never leaves an organisation without something to click on', () => {
    const card = organizationCard(organization({ title: '' }));

    expect(card.title).toBe('produkt');
  });

  it('marks a team apart from an organisation at a glance', () => {
    expect(teamCard(team()).glyph).not.toBe(organizationCard(organization()).glyph);
    expect(teamCard(team()).level).toBe('team');
  });

  it('gives a team without a description one a person can still read', () => {
    expect(teamCard(team({ description: '' })).subtitle).toBe('Agenten-Team');
  });
});

describe('searching across both levels', () => {
  it('finds a card by its title, its subtitle or its level', () => {
    const card = organizationCard(organization());

    expect(matchesSearch(card, 'produkt')).toBe(true);
    expect(matchesSearch(card, 'Bereiche')).toBe(true);
    expect(matchesSearch(card, 'organisation')).toBe(true);
    expect(matchesSearch(card, 'zebra')).toBe(false);
  });

  it('shows everything when nothing was typed', () => {
    expect(matchesSearch(teamCard(team()), '   ')).toBe(true);
  });
});

describe('the structure of an organisation', () => {
  it('keeps the depth the Hub assigned rather than recomputing one', () => {
    const rows = structureNodes(page([node({ depth: 3 })]));

    expect(rows[0].depth).toBe(3);
  });

  it('translates every kind the Hub sends into a level with its own mark', () => {
    const kinds = ['organization', 'coordination_unit', 'value_stream', 'team', 'role_slot', 'assignment'];

    const rows = structureNodes(page(kinds.map((kind, index) => node({ id: `n-${index}`, kind }))));

    expect(rows).toHaveLength(6);
    expect(new Set(rows.map(row => row.glyph)).size).toBe(6);
  });

  it('drops a kind it has no level for rather than inventing one', () => {
    const rows = structureNodes(page([node({ kind: 'something_new' }), node({ id: 'n-2' })]));

    expect(rows.map(row => row.id)).toEqual(['n-2']);
  });

  it('names a node the Hub sent without a label so it can still be pointed at', () => {
    const rows = structureNodes(page([node({ label: '   ' })]));

    expect(rows[0].label).toBe('team.alpha');
  });

  it('carries the runtime status the overlay knows for a node', () => {
    const overlay = { nodes: [{ node_id: 'n-1', status: { state: 'blocked', label: 'Blockiert' } }] };

    const rows = structureNodes(page([node()], overlay));

    expect(rows[0].status).toBe('Blockiert');
  });

  it('leaves the status empty rather than guessing when no overlay covers a node', () => {
    expect(structureNodes(page([node()])).at(0)?.status).toBe('');
  });

  it('refuses a depth beyond what this view is meant to render', () => {
    expect(structureNodes(page([node({ depth: 99 })]))).toEqual([]);
  });

  it('treats a missing page as an empty structure, not as an error', () => {
    expect(structureNodes(null)).toEqual([]);
    expect(structureNodes(page([]))).toEqual([]);
  });

  it('counts what an organisation holds, by level', () => {
    const rows = structureNodes(
      page([node({ id: 'a', kind: 'team' }), node({ id: 'b', kind: 'team' }), node({ id: 'c', kind: 'role_slot' })]),
    );

    expect(countByLevel(rows)).toEqual({ team: 2, role_slot: 1 });
  });
});

describe('clicking down through the structure', () => {
  it('finds what a node directly contains', () => {
    const rows = structureNodes(
      page([
        node({ id: 'unit', kind: 'coordination_unit', parent_id: null }),
        node({ id: 'team', kind: 'team', parent_id: 'unit' }),
        node({ id: 'slot', kind: 'role_slot', parent_id: 'team' }),
      ]),
    );

    expect(childrenOf(rows, 'unit').map(row => row.id)).toEqual(['team']);
    expect(childrenOf(rows, null).map(row => row.id)).toEqual(['unit']);
  });

  it('finds everything below a node, however deep', () => {
    const rows = structureNodes(
      page([
        node({ id: 'unit', kind: 'coordination_unit', parent_id: null }),
        node({ id: 'team', kind: 'team', parent_id: 'unit' }),
        node({ id: 'slot', kind: 'role_slot', parent_id: 'team' }),
        node({ id: 'other', kind: 'team', parent_id: null }),
      ]),
    );

    expect(descendantsOf(rows, 'unit').map(row => row.id)).toEqual(['team', 'slot']);
    expect(descendantsOf(rows, 'other')).toEqual([]);
  });

  it('asks a team about its own tasks and an assignment about its agent', () => {
    const rows = structureNodes(
      page([
        node({ id: 'team', kind: 'team' }),
        node({ id: 'a-1', kind: 'assignment', metadata: { agent_url: 'http://agent-a' } }),
      ]),
    );

    expect(workScopeFor(rows[0])).toEqual({ level: 'team', team_id: 'team' });
    expect(workScopeFor(rows[1])).toEqual({ level: 'agent', agent: 'http://agent-a' });
  });

  it('asks a unit and a value stream about their own slice', () => {
    const rows = structureNodes(
      page([
        node({ id: 'unit', kind: 'coordination_unit' }),
        node({ id: 'stream', kind: 'value_stream' }),
        node({ id: 'slot', kind: 'role_slot' }),
      ]),
    );

    expect(workScopeFor(rows[0])).toEqual({ level: 'unit', unit_id: 'unit' });
    expect(workScopeFor(rows[1])).toEqual({ level: 'unit', unit_id: 'stream' });
    expect(workScopeFor(rows[2])).toEqual({ level: 'role_slot', role_slot_id: 'slot' });
  });

  it('falls back to the organisation when standing on nothing in particular', () => {
    const rows = structureNodes(page([node({ id: 'org', kind: 'organization' })]));

    expect(workScopeFor(rows[0])).toEqual({ level: 'organization' });
    expect(workScopeFor(null)).toEqual({ level: 'organization' });
  });

  it('does not guess an agent from a label when the node names none', () => {
    const rows = structureNodes(page([node({ id: 'a-1', kind: 'assignment', label: 'Fritz' })]));

    expect(rows[0].agent_ref).toBe('');
    expect(workScopeFor(rows[0])).toEqual({ level: 'agent', agent: '' });
  });

  it('falls back to the assignment id when no agent url was sent', () => {
    const rows = structureNodes(page([node({ id: 'a-1', kind: 'assignment', assignment_id: 'as-9' })]));

    expect(rows[0].agent_ref).toBe('as-9');
  });
});
