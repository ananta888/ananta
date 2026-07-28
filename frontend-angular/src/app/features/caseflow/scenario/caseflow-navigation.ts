import { AppNavGroup, AppNavItem } from '../../../models/route-metadata';
import { CaseFlowScenarioDefinition } from './caseflow-scenario.models';

export function withCaseFlowScenarios(
  groups: AppNavGroup[],
  scenarios: readonly CaseFlowScenarioDefinition[],
): AppNavGroup[] {
  return groups.map(group => {
    if (group.label !== 'CaseFlow') return group;

    const staticPaths = new Set(group.items.map(item => item.path));
    const scenarioItems: AppNavItem[] = scenarios
      .map((scenario, index) => ({
        label: scenario.title,
        path: scenario.route || `/caseflow/scenario/${encodeURIComponent(scenario.id)}`,
        area: 'Operate' as const,
        navGroup: 'CaseFlow',
        navOrder: 10 + index,
        simpleNav: true,
      }))
      .filter(item => !staticPaths.has(item.path));

    return {
      ...group,
      items: [...group.items, ...scenarioItems],
    };
  });
}
