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

export function matchesSearch(card: Readonly<OverviewCard>, needle: string): boolean {
  const text = needle.trim().toLocaleLowerCase();
  if (!text) return true;
  return `${card.title} ${card.subtitle} ${card.level_label}`.toLocaleLowerCase().includes(text);
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
