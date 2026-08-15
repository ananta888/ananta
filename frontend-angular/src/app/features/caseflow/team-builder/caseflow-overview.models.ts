/**
 * One list of everything a person can open, across both levels.
 *
 * An organisation and an agent team are the same idea at different sizes: a
 * structure of who does what. They live in two subsystems with two vocabularies
 * — organisations have units, value streams, teams and role slots; a team has
 * agents on a graph — and until now in two screens that never mentioned each
 * other. This turns both into the same card so one overview can offer them
 * side by side, each marked with the level it belongs to.
 */

import type {
  OrganizationSummary,
  OrganizationTopologyNode,
  OrganizationTopologyPage,
} from '../../organizations/models/organization-topology.models';
import type { SavedGraphSummary } from '../../visual-process/visual-process-api.service';
import { type CaseFlowLevel, levelGlyph, levelLabel } from '../agent-canvas/caseflow-agent-glyphs';
import type { WorkScope } from './caseflow-work.models';

export interface OverviewCard {
  readonly id: string;
  readonly level: CaseFlowLevel;
  readonly glyph: string;
  readonly level_label: string;
  readonly title: string;
  readonly subtitle: string;
  /** Whether opening this card leads anywhere, rather than being decoration. */
  readonly openable: boolean;
}

/** One node of an organisation, as a row a person can read. */
export interface OverviewStructureNode {
  readonly id: string;
  readonly level: CaseFlowLevel;
  readonly glyph: string;
  readonly label: string;
  readonly depth: number;
  readonly status: string;
  readonly parent_id: string | null;
  /** The agent an assignment node points at, empty when it names none. */
  readonly agent_ref: string;
  /** Whether this node holds anything worth expanding into. */
  readonly child_count: number;
}

const NODE_LEVELS: Readonly<Record<string, CaseFlowLevel>> = {
  organization: 'organization',
  coordination_unit: 'unit',
  value_stream: 'value_stream',
  team: 'team',
  role_slot: 'role_slot',
  assignment: 'agent',
};

const MAX_DEPTH = 12;

export function organizationCard(organization: Readonly<OrganizationSummary>): OverviewCard {
  const teams = organization.team_count ?? 0;
  const units = organization.unit_count ?? 0;
  return {
    id: organization.id,
    level: 'organization',
    glyph: levelGlyph('organization'),
    level_label: levelLabel('organization'),
    title: organization.title || organization.key || organization.id,
    subtitle: `${teams} Teams · ${units} Bereiche · ${lifecycleLabel(organization.lifecycle)}`,
    openable: true,
  };
}

export function teamCard(team: Readonly<SavedGraphSummary>): OverviewCard {
  return {
    id: team.id,
    level: 'team',
    glyph: levelGlyph('team'),
    level_label: levelLabel('team'),
    title: team.name || team.id,
    subtitle: team.description || 'Agenten-Team',
    openable: true,
  };
}

/**
 * The organisation's structure, flattened into readable rows.
 *
 * The topology arrives as nodes with a parent and a depth the Hub assigned;
 * both are kept rather than recomputed, so what a person sees is the shape the
 * Hub actually holds. A node too deep to be meant for this view is dropped
 * rather than rendered at an arbitrary indent.
 */
export function structureNodes(page: Readonly<OrganizationTopologyPage> | null): readonly OverviewStructureNode[] {
  if (!page?.nodes?.length) return [];
  const statuses = new Map(
    (page.runtime_overlay?.nodes ?? []).map(entry => [entry.node_id, entry.status?.label ?? '']),
  );
  const rows: OverviewStructureNode[] = [];
  for (const node of page.nodes) {
    const level = NODE_LEVELS[node.kind];
    if (!level) continue;
    const depth = Number.isFinite(node.depth) ? Math.max(0, Math.trunc(node.depth)) : 0;
    if (depth > MAX_DEPTH) continue;
    rows.push({
      id: node.id,
      level,
      glyph: levelGlyph(level),
      label: readableLabel(node),
      depth,
      status: statuses.get(node.id) ?? '',
      parent_id: node.parent_id ?? null,
      agent_ref: agentRef(node),
      child_count: Number.isFinite(node.child_count) ? Math.max(0, Math.trunc(node.child_count)) : 0,
    });
  }
  return rows;
}

/** How many of each level an organisation holds, for a one-line summary. */
export function countByLevel(nodes: readonly OverviewStructureNode[]): Readonly<Record<string, number>> {
  const counts: Record<string, number> = {};
  for (const node of nodes) counts[node.level] = (counts[node.level] ?? 0) + 1;
  return counts;
}

/** The parts one node of the structure directly contains. */
export function childrenOf(
  nodes: readonly OverviewStructureNode[],
  parentId: string | null,
): readonly OverviewStructureNode[] {
  return nodes.filter(node => (node.parent_id ?? null) === parentId);
}

/** Everything below one node, at any depth, in the order the Hub sent. */
export function descendantsOf(
  nodes: readonly OverviewStructureNode[],
  rootId: string,
): readonly OverviewStructureNode[] {
  const included = new Set([rootId]);
  const found: OverviewStructureNode[] = [];
  // One forward pass suffices: the Hub sends parents before their children,
  // and a node whose parent never arrives is simply not below this root.
  for (const node of nodes) {
    if (node.id === rootId) continue;
    if (node.parent_id && included.has(node.parent_id)) {
      included.add(node.id);
      found.push(node);
    }
  }
  return found;
}

/**
 * Which work question a node can be asked.
 *
 * A team is asked about its own tasks, an assignment about the agent holding
 * it, and everything else about the organisation around it. A node that
 * carries no usable identity yields a scope the panel will refuse rather than
 * one it would answer too broadly.
 */
export function workScopeFor(node: Readonly<OverviewStructureNode> | null): WorkScope {
  if (!node) return { level: 'organization' };
  switch (node.level) {
    case 'team':
      return { level: 'team', team_id: node.id };
    case 'unit':
    case 'value_stream':
      return { level: 'unit', unit_id: node.id };
    case 'role_slot':
      return { level: 'role_slot', role_slot_id: node.id };
    case 'agent':
      return { level: 'agent', agent: node.agent_ref };
    default:
      return { level: 'organization' };
  }
}

export function matchesSearch(card: Readonly<OverviewCard>, needle: string): boolean {
  const text = needle.trim().toLocaleLowerCase();
  if (!text) return true;
  return `${card.title} ${card.subtitle} ${card.level_label}`.toLocaleLowerCase().includes(text);
}

/**
 * The agent an assignment points at.
 *
 * Only the explicit identifiers count. Guessing an agent from a label would
 * put another agent's work under this one's name.
 */
function agentRef(node: Readonly<OrganizationTopologyNode>): string {
  const metadata = (node.metadata ?? {}) as Record<string, unknown>;
  for (const key of ['agent_url', 'agent_id']) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return typeof node.assignment_id === 'string' ? node.assignment_id.trim() : '';
}

function readableLabel(node: Readonly<OrganizationTopologyNode>): string {
  const label = (node.label ?? '').trim();
  // Never blank: a row a person cannot name is a row they cannot act on.
  return label || node.stable_key || node.id;
}

function lifecycleLabel(lifecycle: string): string {
  return LIFECYCLE_LABELS[lifecycle] ?? lifecycle;
}

const LIFECYCLE_LABELS: Readonly<Record<string, string>> = {
  draft: 'Entwurf',
  validated: 'geprüft',
  active: 'aktiv',
  paused: 'pausiert',
  completed: 'abgeschlossen',
  archived: 'archiviert',
};
