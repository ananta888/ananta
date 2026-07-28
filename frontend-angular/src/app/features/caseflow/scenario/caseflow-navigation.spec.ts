import { describe, expect, it } from 'vitest';
import { AppNavGroup } from '../../../models/route-metadata';
import { CASEFLOW_UI_SCHEMA, CaseFlowScenarioDefinition } from './caseflow-scenario.models';
import { withCaseFlowScenarios } from './caseflow-navigation';

const groups: AppNavGroup[] = [
  {
    label: 'Arbeiten',
    items: [{ label: 'Arbeitsbereich', path: '/workspace', area: 'Operate' }],
  },
  {
    label: 'CaseFlow',
    items: [{
      label: 'CaseFlow Studio',
      path: '/caseflow/studio',
      area: 'Configure',
      separatorAfter: true,
    }],
  },
];

function scenario(
  id: string,
  title: string,
  route?: string,
): CaseFlowScenarioDefinition {
  return {
    schema: CASEFLOW_UI_SCHEMA,
    id,
    title,
    description: '',
    icon: '',
    caseType: id,
    workflowGraphId: id,
    route,
    tags: [],
    pages: [],
  };
}

describe('CaseFlow navigation projection', () => {
  it('keeps Studio first and appends every CaseFlow below it', () => {
    const result = withCaseFlowScenarios(groups, [
      scenario('bewerbungen', 'Bewerbungen', '/caseflow/jobs'),
      scenario('classroom', 'Classroom', '/caseflow/classroom'),
      scenario('kunden', 'Kundenaufnahme'),
    ]);
    const items = result.find(group => group.label === 'CaseFlow')?.items || [];

    expect(items.map(item => [item.label, item.path])).toEqual([
      ['CaseFlow Studio', '/caseflow/studio'],
      ['Bewerbungen', '/caseflow/jobs'],
      ['Classroom', '/caseflow/classroom'],
      ['Kundenaufnahme', '/caseflow/scenario/kunden'],
    ]);
    expect(items[0].separatorAfter).toBe(true);
  });

  it('does not change unrelated navigation groups or duplicate static paths', () => {
    const result = withCaseFlowScenarios(groups, [
      scenario('studio', 'Studio duplicate', '/caseflow/studio'),
    ]);

    expect(result[0]).toBe(groups[0]);
    expect(result[1].items.map(item => item.path)).toEqual(['/caseflow/studio']);
  });
});
