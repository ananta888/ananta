export const CASEFLOW_UI_EXTENSION = 'ananta.caseflow.ui';
export const CASEFLOW_UI_SCHEMA = 'ananta.caseflow.ui/v1';

export type CaseFlowBlockKind =
  | 'summary'
  | 'metrics'
  | 'case-list'
  | 'actions'
  | 'artifacts'
  | 'workflow';

export interface CaseFlowBlockDefinition {
  id: string;
  kind: CaseFlowBlockKind;
  title: string;
  description?: string;
}

export interface CaseFlowPageDefinition {
  id: string;
  title: string;
  blocks: CaseFlowBlockDefinition[];
}

export interface CaseFlowScenarioDefinition {
  schema: typeof CASEFLOW_UI_SCHEMA;
  id: string;
  title: string;
  description: string;
  icon: string;
  caseType: string;
  workflowGraphId: string;
  route?: string;
  tags: string[];
  pages: CaseFlowPageDefinition[];
}

export interface CaseFlowScenarioDraft {
  id: string;
  title: string;
  description: string;
  icon: string;
  caseType: string;
}

export function isCaseFlowScenarioDefinition(value: unknown): value is CaseFlowScenarioDefinition {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CaseFlowScenarioDefinition>;
  return candidate.schema === CASEFLOW_UI_SCHEMA
    && typeof candidate.id === 'string'
    && typeof candidate.title === 'string'
    && typeof candidate.description === 'string'
    && typeof candidate.icon === 'string'
    && typeof candidate.caseType === 'string'
    && typeof candidate.workflowGraphId === 'string'
    && Array.isArray(candidate.tags)
    && candidate.tags.every(tag => typeof tag === 'string')
    && Array.isArray(candidate.pages)
    && candidate.pages.length > 0
    && candidate.pages.every(page =>
      Boolean(page)
      && typeof page.id === 'string'
      && typeof page.title === 'string'
      && Array.isArray(page.blocks)
      && page.blocks.length > 0
      && page.blocks.every(block =>
        Boolean(block)
        && typeof block.id === 'string'
        && typeof block.title === 'string'
        && isCaseFlowBlockKind(block.kind)));
}

function isCaseFlowBlockKind(value: unknown): value is CaseFlowBlockKind {
  return ['summary', 'metrics', 'case-list', 'actions', 'artifacts', 'workflow'].includes(String(value));
}
