export interface ComponentHotspot {
  path: string;
  lines: number;
  priority: 'critical' | 'high' | 'medium';
  nextAction: string;
}

export const COMPONENT_HOTSPOTS: ComponentHotspot[] = [
  {
    path: 'frontend-angular/src/app/components/settings.component.ts',
    lines: 2307,
    priority: 'critical',
    nextAction: 'Split account, LLM, quality and system sections into feature panels.',
  },
  {
    path: 'frontend-angular/src/app/components/artifacts.component.ts',
    lines: 1135,
    priority: 'high',
    nextAction: 'Extract artifact flow, collection profile and workspace panels.',
  },
  {
    path: 'frontend-angular/src/app/components/task-detail.component.ts',
    lines: 1103,
    priority: 'high',
    nextAction: 'Extract execution, proposal, timeline and workspace panels.',
  },
  {
    path: 'frontend-angular/src/app/components/teams.component.ts',
    lines: 1096,
    priority: 'high',
    nextAction: 'Extract blueprint, member editor and role mapping panels.',
  },
];

export function highestPriorityHotspots(limit = 4): ComponentHotspot[] {
  return COMPONENT_HOTSPOTS.slice(0, limit);
}
