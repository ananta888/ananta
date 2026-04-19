export type AppRouteArea = 'Operate' | 'Automate' | 'Configure' | 'System' | 'General';

export interface AppRouteMeta {
  label: string;
  area: AppRouteArea;
  navGroup?: string;
  navOrder?: number;
  adminOnly?: boolean;
}

export interface AppNavItem extends AppRouteMeta {
  path: string;
}

export interface AppNavGroup {
  label: string;
  items: AppNavItem[];
}

export const APP_ROUTE_META: Record<string, AppRouteMeta> = {
  dashboard: { label: 'Dashboard', area: 'Operate', navGroup: 'Betrieb', navOrder: 10 },
  agents: { label: 'Agenten', area: 'Operate', navGroup: 'Betrieb', navOrder: 20 },
  board: { label: 'Board', area: 'Operate', navGroup: 'Betrieb', navOrder: 30 },
  operations: { label: 'Operationen', area: 'Operate', navGroup: 'Betrieb', navOrder: 40 },
  artifacts: { label: 'Artifacts', area: 'Operate', navGroup: 'Betrieb', navOrder: 50 },
  archived: { label: 'Archiv', area: 'Operate', navGroup: 'Betrieb', navOrder: 60 },
  graph: { label: 'Graph', area: 'Operate', navGroup: 'Betrieb', navOrder: 70 },
  'auto-planner': { label: 'Auto-Planner', area: 'Automate', navGroup: 'Automatisierung', navOrder: 10 },
  webhooks: { label: 'Webhooks', area: 'Automate', navGroup: 'Automatisierung', navOrder: 20 },
  templates: { label: 'Templates', area: 'Configure', navGroup: 'Konfiguration', navOrder: 10 },
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

export function buildNavGroups(role?: string | null): AppNavGroup[] {
  const grouped = new Map<string, AppNavItem[]>();
  for (const [path, meta] of Object.entries(APP_ROUTE_META)) {
    if (!meta.navGroup) continue;
    if (meta.adminOnly && role !== 'admin') continue;
    const items = grouped.get(meta.navGroup) || [];
    items.push({ path: `/${path}`, ...meta });
    grouped.set(meta.navGroup, items);
  }

  return Array.from(grouped.entries()).map(([label, items]) => ({
    label,
    items: items.sort((left, right) => Number(left.navOrder || 0) - Number(right.navOrder || 0)),
  }));
}
