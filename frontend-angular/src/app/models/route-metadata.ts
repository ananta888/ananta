export type AppRouteArea = 'Operate' | 'Automate' | 'Configure' | 'System' | 'General';

export interface AppRouteMeta {
  label: string;
  area: AppRouteArea;
  navGroup?: string;
  navOrder?: number;
  adminOnly?: boolean;
  simpleNav?: boolean;
}

export interface AppNavItem extends AppRouteMeta {
  path: string;
}

export interface AppNavGroup {
  label: string;
  items: AppNavItem[];
}

export const APP_ROUTE_META: Record<string, AppRouteMeta> = {
  dashboard: { label: 'Start', area: 'Operate', navGroup: 'Arbeiten', navOrder: 10, simpleNav: true },
  agents: { label: 'Agenten', area: 'Operate', navGroup: 'Betrieb', navOrder: 20 },
  board: { label: 'Aufgaben', area: 'Operate', navGroup: 'Arbeiten', navOrder: 20, simpleNav: true },
  operations: { label: 'Operationen', area: 'Operate', navGroup: 'Betrieb', navOrder: 40 },
  artifacts: { label: 'Ergebnisse', area: 'Operate', navGroup: 'Arbeiten', navOrder: 30, simpleNav: true },
  archived: { label: 'Archiv', area: 'Operate', navGroup: 'Betrieb', navOrder: 60 },
  graph: { label: 'Graph', area: 'Operate', navGroup: 'Betrieb', navOrder: 70 },
  'auto-planner': { label: 'Auto-Planner', area: 'Automate', navGroup: 'Automatisierung', navOrder: 10 },
  webhooks: { label: 'Webhooks', area: 'Automate', navGroup: 'Automatisierung', navOrder: 20 },
  templates: { label: 'Vorlagen', area: 'Configure', navGroup: 'Arbeiten', navOrder: 40, simpleNav: true },
  teams: { label: 'Teams', area: 'Configure', navGroup: 'Konfiguration', navOrder: 20 },
  'audit-log': { label: 'Audit-Logs', area: 'System', navGroup: 'Konfiguration', navOrder: 30, adminOnly: true },
  settings: { label: 'Einstellungen', area: 'System', navGroup: 'Konfiguration', navOrder: 40 },
  panel: { label: 'Agent Panel', area: 'System' },
  task: { label: 'Task Details', area: 'Operate' },
  goal: { label: 'Goal Details', area: 'Operate' },
};

export function routeDataFor(path: keyof typeof APP_ROUTE_META): { breadcrumb: string; area: AppRouteArea } {
  const meta = APP_ROUTE_META[path];
  return { breadcrumb: meta.label, area: meta.area };
}

export type AppShellMode = 'simple' | 'advanced';

export function buildNavGroups(role?: string | null, mode: AppShellMode = 'simple'): AppNavGroup[] {
  const grouped = new Map<string, AppNavItem[]>();
  for (const [path, meta] of Object.entries(APP_ROUTE_META)) {
    if (!meta.navGroup) continue;
    if (meta.adminOnly && role !== 'admin') continue;
    if (mode === 'simple' && !meta.simpleNav) continue;
    const items = grouped.get(meta.navGroup) || [];
    items.push({ path: `/${path}`, ...meta });
    grouped.set(meta.navGroup, items);
  }

  return Array.from(grouped.entries()).map(([label, items]) => ({
    label,
    items: items.sort((left, right) => Number(left.navOrder || 0) - Number(right.navOrder || 0)),
  }));
}
