import {
  CASEFLOW_AGENT_ICON_ALLOWLIST,
  CaseFlowAgentIcon,
  CaseFlowAgentCanvasResult,
  agentCanvasFailure,
  agentCanvasSuccess,
  isAllowedCaseFlowAgentIcon,
} from './caseflow-agent-canvas.models';

export {
  CASEFLOW_AGENT_ICON_ALLOWLIST,
  isAllowedCaseFlowAgentIcon,
} from './caseflow-agent-canvas.models';
export type { CaseFlowAgentIcon } from './caseflow-agent-canvas.models';

export const CASEFLOW_AGENT_ROLE_CATEGORIES = [
  'software',
  'creative',
  'business',
  'generic',
] as const;

export type CaseFlowAgentRoleCategory = typeof CASEFLOW_AGENT_ROLE_CATEGORIES[number];

const ROLE_CATALOG = [
  { id: 'architect', category: 'software', label: 'Architect', default_icon: 'architecture' },
  { id: 'developer', category: 'software', label: 'Developer', default_icon: 'code' },
  { id: 'frontend', category: 'software', label: 'Frontend', default_icon: 'web' },
  { id: 'backend', category: 'software', label: 'Backend', default_icon: 'dns' },
  { id: 'devops', category: 'software', label: 'DevOps', default_icon: 'cloud' },
  { id: 'tester', category: 'software', label: 'Tester', default_icon: 'bug_report' },
  { id: 'security', category: 'software', label: 'Security', default_icon: 'security' },
  { id: 'reviewer', category: 'software', label: 'Reviewer', default_icon: 'rate_review' },
  { id: 'product_owner', category: 'software', label: 'Product Owner', default_icon: 'assignment_turned_in' },
  { id: 'scrum_master', category: 'software', label: 'Scrum Master', default_icon: 'groups' },

  { id: 'artist', category: 'creative', label: 'Artist', default_icon: 'palette' },
  { id: 'designer', category: 'creative', label: 'Designer', default_icon: 'design_services' },
  { id: 'ux', category: 'creative', label: 'UX', default_icon: 'accessibility_new' },
  { id: 'writer', category: 'creative', label: 'Writer', default_icon: 'edit_note' },
  { id: 'video', category: 'creative', label: 'Video', default_icon: 'videocam' },
  { id: 'audio', category: 'creative', label: 'Audio', default_icon: 'graphic_eq' },

  { id: 'marketing', category: 'business', label: 'Marketing', default_icon: 'campaign' },
  { id: 'sales', category: 'business', label: 'Sales', default_icon: 'point_of_sale' },
  { id: 'finance', category: 'business', label: 'Finance', default_icon: 'account_balance' },
  { id: 'legal', category: 'business', label: 'Legal', default_icon: 'gavel' },
  { id: 'hr', category: 'business', label: 'HR', default_icon: 'badge' },
  { id: 'business_analyst', category: 'business', label: 'Business Analyst', default_icon: 'analytics' },
  { id: 'researcher', category: 'business', label: 'Researcher', default_icon: 'science' },
  { id: 'project_manager', category: 'business', label: 'Project Manager', default_icon: 'event_note' },

  { id: 'lead', category: 'generic', label: 'Lead', default_icon: 'star' },
  { id: 'specialist', category: 'generic', label: 'Specialist', default_icon: 'engineering' },
  { id: 'critic', category: 'generic', label: 'Critic', default_icon: 'rule' },
  { id: 'approver', category: 'generic', label: 'Approver', default_icon: 'approval' },
  { id: 'observer', category: 'generic', label: 'Observer', default_icon: 'visibility' },
  { id: 'custom', category: 'generic', label: 'Custom', default_icon: 'person' },
] as const satisfies readonly {
  id: string;
  category: CaseFlowAgentRoleCategory;
  label: string;
  default_icon: CaseFlowAgentIcon;
}[];

export type CaseFlowAgentRoleId = typeof ROLE_CATALOG[number]['id'];
export type CaseFlowKnownRoleId = Exclude<CaseFlowAgentRoleId, 'custom'>;
export type CaseFlowAgentRoleDefinition = typeof ROLE_CATALOG[number];

export const CASEFLOW_AGENT_ROLE_CATALOG: readonly CaseFlowAgentRoleDefinition[] = ROLE_CATALOG;

export const CASEFLOW_AGENT_ROLES_BY_CATEGORY: Readonly<
  Record<CaseFlowAgentRoleCategory, readonly CaseFlowAgentRoleDefinition[]>
> = Object.freeze(Object.fromEntries(
  CASEFLOW_AGENT_ROLE_CATEGORIES.map(category => [
    category,
    Object.freeze(ROLE_CATALOG.filter(role => role.category === category)),
  ]),
) as Record<CaseFlowAgentRoleCategory, readonly CaseFlowAgentRoleDefinition[]>);

export type CaseFlowAgentRoleSelection =
  | {
    role_id: CaseFlowKnownRoleId;
    icon?: CaseFlowAgentIcon;
  }
  | {
    role_id: 'custom';
    custom_name: string;
    icon: CaseFlowAgentIcon;
  };

export interface ResolvedCaseFlowAgentRole {
  canonical_role: string;
  role_preset: CaseFlowAgentRoleId;
  icon: CaseFlowAgentIcon;
}

const ROLES = new Map<string, CaseFlowAgentRoleDefinition>(
  ROLE_CATALOG.map(role => [role.id, role]),
);

export function isCaseFlowAgentRoleId(value: unknown): value is CaseFlowAgentRoleId {
  return typeof value === 'string' && ROLES.has(value);
}

export function roleDefinitionFor(role: string | undefined): CaseFlowAgentRoleDefinition {
  return ROLES.get(role ?? '') ?? ROLES.get('custom')!;
}

export function validateRoleSelection(
  selection: CaseFlowAgentRoleSelection,
): CaseFlowAgentCanvasResult<ResolvedCaseFlowAgentRole> {
  const definition = ROLES.get(selection.role_id);
  if (!definition) {
    return agentCanvasFailure({
      code: 'agent_role_invalid',
      path: '/role_id',
      message: `Role preset "${String(selection.role_id)}" is not in the CaseFlow v1 catalog.`,
    });
  }

  if (selection.role_id === 'custom') {
    const customName = selection.custom_name?.trim();
    if (!customName) {
      return agentCanvasFailure({
        code: 'agent_role_invalid',
        path: '/custom_name',
        message: 'A custom role requires a non-empty name.',
      });
    }
    if (!isAllowedCaseFlowAgentIcon(selection.icon)) {
      return agentCanvasFailure({
        code: 'agent_icon_not_allowed',
        path: '/icon',
        message: `Icon "${String(selection.icon)}" is not in the CaseFlow icon allowlist.`,
      });
    }
    return agentCanvasSuccess({
      canonical_role: customName,
      role_preset: 'custom',
      icon: selection.icon,
    });
  }

  const icon = selection.icon ?? definition.default_icon;
  if (!isAllowedCaseFlowAgentIcon(icon)) {
    return agentCanvasFailure({
      code: 'agent_icon_not_allowed',
      path: '/icon',
      message: `Icon "${String(icon)}" is not in the CaseFlow icon allowlist.`,
    });
  }
  return agentCanvasSuccess({
    canonical_role: definition.id,
    role_preset: definition.id,
    icon,
  });
}
