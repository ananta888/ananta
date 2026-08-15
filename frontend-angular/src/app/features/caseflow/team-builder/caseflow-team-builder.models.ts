/**
 * Turning a chosen template into a workflow a team can actually run.
 *
 * The three editors that already exist each ask a person to know something
 * first: which process to select, which node kinds exist, which notation to
 * use. This step asks only two questions — which template, and what is the
 * team called — and produces a graph the same editors can then refine.
 *
 * Everything here is pure so the shape of what gets saved can be asserted
 * without a browser.
 */

export const TEAM_TEMPLATE_KIND_TEAM = 'team';
export const TEAM_TEMPLATE_KIND_PROCESS = 'process';

export const CASEFLOW_AGENT_CANVAS_EXTENSION = 'ananta.caseflow.agent-canvas';
export const CASEFLOW_AGENT_CANVAS_SCHEMA_V1 = 'ananta.caseflow.agent-canvas/v1';

/** Laid out left to right so the first agent reads first. */
const STEP_X_ORIGIN = 120;
const STEP_X_SPACING = 260;
const STEP_Y = 160;
const DEFAULT_STEP_KIND = 'coding';
const MAX_TEAM_NAME_CHARS = 120;

export interface TeamTemplateRole {
  readonly role_id: string;
  readonly display_name: string;
}

export interface TeamTemplate {
  readonly template_id: string;
  readonly kind: string;
  readonly source_id: string;
  readonly display_name: string;
  readonly description: string;
  readonly roles: readonly TeamTemplateRole[];
  readonly aliases: readonly string[];
  readonly agent_count: number;
}

export interface TeamTemplateCatalogResponse {
  readonly schema: string;
  readonly templates: readonly TeamTemplate[];
}

export interface DraftAgent {
  readonly step_id: string;
  readonly name: string;
  readonly role_id: string;
  readonly role_name: string;
}

export interface TeamDraft {
  readonly team_name: string;
  readonly template_id: string;
  readonly agents: readonly DraftAgent[];
}

export type TeamBuilderIssueCode =
  | 'team_name_required'
  | 'team_name_too_long'
  | 'team_needs_an_agent'
  | 'agent_name_required'
  | 'agent_names_must_differ';

export interface TeamBuilderIssue {
  readonly code: TeamBuilderIssueCode;
  readonly message: string;
  readonly step_id?: string;
}

/** Steps and edges the visual-process editors understand. */
export interface BuiltGraph {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly version: string;
  readonly steps: readonly unknown[];
  readonly edges: readonly unknown[];
  readonly tags: readonly string[];
  readonly extensions: Record<string, unknown>;
}

/** Build the editable draft a chosen template starts you with. */
export function draftFromTemplate(template: TeamTemplate, teamName = ''): TeamDraft {
  const agents = template.roles.map((role, index) => ({
    step_id: stepIdFor(index),
    // The role name is the agent's name until a person gives it one, which is
    // usually what they would have typed anyway.
    name: role.display_name,
    role_id: role.role_id,
    role_name: role.display_name,
  }));
  return {
    team_name: teamName || template.display_name,
    template_id: template.template_id,
    agents,
  };
}

/** Add one more agent to a draft, named after the role it fills. */
export function withAddedAgent(draft: TeamDraft, role: TeamTemplateRole): TeamDraft {
  const used = new Set(draft.agents.map(agent => agent.step_id));
  let index = draft.agents.length;
  while (used.has(stepIdFor(index))) index += 1;
  return {
    ...draft,
    agents: [
      ...draft.agents,
      { step_id: stepIdFor(index), name: uniqueName(draft, role.display_name), role_id: role.role_id, role_name: role.display_name },
    ],
  };
}

export function withRemovedAgent(draft: TeamDraft, stepId: string): TeamDraft {
  return { ...draft, agents: draft.agents.filter(agent => agent.step_id !== stepId) };
}

export function withRenamedAgent(draft: TeamDraft, stepId: string, name: string): TeamDraft {
  return {
    ...draft,
    agents: draft.agents.map(agent => (agent.step_id === stepId ? { ...agent, name } : agent)),
  };
}

/**
 * What is wrong with a draft, in the order a person would fix it.
 *
 * Duplicate names are refused rather than silently suffixed: two agents
 * called the same thing is exactly the confusion this view exists to prevent
 * once they start talking to each other.
 */
export function validateDraft(draft: TeamDraft): readonly TeamBuilderIssue[] {
  const issues: TeamBuilderIssue[] = [];
  const teamName = draft.team_name.trim();
  if (!teamName) {
    issues.push({ code: 'team_name_required', message: 'Das Team braucht einen Namen.' });
  } else if (teamName.length > MAX_TEAM_NAME_CHARS) {
    issues.push({ code: 'team_name_too_long', message: `Höchstens ${MAX_TEAM_NAME_CHARS} Zeichen.` });
  }
  if (!draft.agents.length) {
    issues.push({ code: 'team_needs_an_agent', message: 'Mindestens ein Agent wird gebraucht.' });
  }
  const seen = new Map<string, string>();
  for (const agent of draft.agents) {
    const name = agent.name.trim();
    if (!name) {
      issues.push({ code: 'agent_name_required', message: 'Jeder Agent braucht einen Namen.', step_id: agent.step_id });
      continue;
    }
    const key = name.toLocaleLowerCase();
    const claimed = seen.get(key);
    if (claimed && claimed !== agent.step_id) {
      issues.push({
        code: 'agent_names_must_differ',
        message: `„${name}" ist doppelt vergeben.`,
        step_id: agent.step_id,
      });
      continue;
    }
    seen.set(key, agent.step_id);
  }
  return issues;
}

/**
 * Build the graph a draft saves as.
 *
 * Agents are chained in the order they were arranged, because a first
 * arrangement everyone can read beats a clever one nobody expects. The
 * editors are there to change it.
 */
export function buildGraph(draft: TeamDraft, graphId: string): BuiltGraph {
  const steps = draft.agents.map((agent, index) => ({
    id: agent.step_id,
    label: agent.name.trim(),
    kind: DEFAULT_STEP_KIND,
    role: agent.role_name,
    io: { inputs: [], outputs: [] },
    position: { x: STEP_X_ORIGIN + index * STEP_X_SPACING, y: STEP_Y },
    policy_hints: [],
    gate: false,
    metadata: { role_id: agent.role_id },
  }));
  const edges = draft.agents.slice(1).map((agent, index) => ({
    id: `edge-${index + 1}`,
    source: draft.agents[index].step_id,
    target: agent.step_id,
    condition: { kind: 'always' },
  }));
  return {
    id: graphId,
    name: draft.team_name.trim(),
    description: `Team aus Vorlage ${draft.template_id}`,
    version: '1.0.0',
    steps,
    edges,
    tags: ['caseflow-team'],
    extensions: {
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: {
        schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
        nodes: Object.fromEntries(draft.agents.map(agent => [agent.step_id, {}])),
      },
    },
  };
}

/** Templates whose size suits a small team, kept first in the gallery. */
export function isSmallTeam(template: TeamTemplate): boolean {
  return template.agent_count >= 3 && template.agent_count <= 10;
}

function stepIdFor(index: number): string {
  return `agent-${index + 1}`;
}

function uniqueName(draft: TeamDraft, preferred: string): string {
  const taken = new Set(draft.agents.map(agent => agent.name.trim().toLocaleLowerCase()));
  if (!taken.has(preferred.toLocaleLowerCase())) return preferred;
  let suffix = 2;
  while (taken.has(`${preferred} ${suffix}`.toLocaleLowerCase())) suffix += 1;
  return `${preferred} ${suffix}`;
}
