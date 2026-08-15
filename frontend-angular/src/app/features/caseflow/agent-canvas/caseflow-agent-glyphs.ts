/**
 * Icons that render without a font being installed.
 *
 * The canvas asks for Material Icons by name, but no icon font is bundled and
 * the stack runs offline, so those names render as the literal words "code",
 * "web", "dns". For a view whose whole point is that a person recognises an
 * agent at a glance, a word is not an icon. These glyphs are text the platform
 * already draws, so they need nothing loaded.
 */

import { CASEFLOW_AGENT_ROLE_CATALOG } from './caseflow-role-catalog';

/** The levels this overview walks down through. */
export type CaseFlowLevel = 'organization' | 'unit' | 'value_stream' | 'team' | 'role_slot' | 'agent';

const LEVEL_GLYPHS: Readonly<Record<CaseFlowLevel, string>> = {
  organization: '🏛️',
  unit: '🧭',
  value_stream: '🔀',
  team: '👥',
  role_slot: '🪑',
  agent: '🤖',
};

const LEVEL_LABELS: Readonly<Record<CaseFlowLevel, string>> = {
  organization: 'Organisation',
  unit: 'Bereich',
  value_stream: 'Wertstrom',
  team: 'Team',
  role_slot: 'Rollenplatz',
  agent: 'Agent',
};

/**
 * One glyph per role the canvas catalog ships, keyed by its Material name.
 *
 * Keying on the icon name rather than the role id means a role added to the
 * catalog with an existing icon is covered without touching this table.
 */
const ICON_GLYPHS: Readonly<Record<string, string>> = {
  account_tree: '🌳',
  architecture: '🏗️',
  code: '💻',
  web: '🖥️',
  dns: '🗄️',
  cloud: '☁️',
  bug_report: '🐞',
  security: '🔒',
  rate_review: '🔍',
  assignment_turned_in: '✅',
  groups: '🧑‍🤝‍🧑',
  palette: '🎨',
  design_services: '✏️',
  accessibility_new: '♿',
  edit_note: '📝',
  videocam: '🎬',
  graphic_eq: '🎧',
  campaign: '📣',
  point_of_sale: '💰',
  account_balance: '🏦',
  gavel: '⚖️',
  badge: '🪪',
  analytics: '📊',
  science: '🔬',
  event_note: '📅',
  star: '⭐',
  engineering: '🛠️',
  rule: '🧐',
  approval: '👍',
  visibility: '👁️',
  person: '🙂',

  // Runtime status, as the runtime projection names them.
  schedule: '⏳',
  play_circle: '▶️',
  check_circle: '✔️',
  error: '❌',
  skip_next: '⏭️',
  cancel: '🚫',
  help_outline: '❓',
};

const FALLBACK_GLYPH = '🤖';

const GLYPHS_BY_ROLE: ReadonlyMap<string, string> = new Map(
  CASEFLOW_AGENT_ROLE_CATALOG.map(role => [role.id, ICON_GLYPHS[role.default_icon] ?? FALLBACK_GLYPH]),
);

/** Status a person can read off a node without opening it. */
const STATUS_GLYPHS: Readonly<Record<string, string>> = {
  pending: '⏳',
  running: '▶️',
  awaiting_approval: '✋',
  success: '✔️',
  error: '❌',
  skipped: '⏭️',
  cancelled: '🚫',
  unknown: '·',
};

export function levelGlyph(level: CaseFlowLevel): string {
  return LEVEL_GLYPHS[level] ?? FALLBACK_GLYPH;
}

export function levelLabel(level: CaseFlowLevel): string {
  return LEVEL_LABELS[level] ?? 'Ebene';
}

/**
 * The glyph for an agent, from its role preset, its icon, or neither.
 *
 * Never blank: an agent with an unknown role still has to be recognisable as
 * an agent.
 */
export function agentGlyph(options: { readonly icon?: string | null; readonly role?: string | null } = {}): string {
  const byIcon = options.icon ? ICON_GLYPHS[options.icon] : undefined;
  if (byIcon) return byIcon;
  const role = (options.role ?? '').trim();
  return GLYPHS_BY_ROLE.get(role) ?? GLYPHS_BY_ROLE.get(role.toLocaleLowerCase()) ?? FALLBACK_GLYPH;
}

/**
 * The glyph for one Material icon name, or a mark that says "unknown".
 *
 * The canvas asks for icons by that name, so this is the one lookup it needs;
 * a name with no glyph must still draw something rather than leave a hole.
 */
export function glyphForIconName(icon: string | null | undefined): string {
  return ICON_GLYPHS[(icon ?? '').trim()] ?? ICON_GLYPHS['help_outline'];
}

export function statusGlyph(status: string | null | undefined): string {
  return STATUS_GLYPHS[(status ?? '').trim()] ?? STATUS_GLYPHS['unknown'];
}

/** Every icon the catalog can ask for, so a gap shows up as a failing test. */
export function knownIconNames(): readonly string[] {
  return Object.keys(ICON_GLYPHS);
}
