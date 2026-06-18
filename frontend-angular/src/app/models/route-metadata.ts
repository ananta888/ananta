export type AppRouteArea = 'Operate' | 'Automate' | 'Configure' | 'System' | 'General';

export interface AppRouteMeta {
  label: string;
  area: AppRouteArea;
  navGroup?: string;
  navOrder?: number;
  adminOnly?: boolean;
  simpleNav?: boolean;
  expertOnly?: boolean;
}

export interface AppNavItem extends AppRouteMeta {
  path: string;
}

export interface AppNavGroup {
  label: string;
  items: AppNavItem[];
}

export const APP_ROUTE_META: Record<string, AppRouteMeta> = {
  workspace: { label: 'Arbeitsbereich', area: 'Operate', navGroup: 'Arbeiten', navOrder: 10, simpleNav: true },
  chats: { label: 'AI Chats', area: 'Operate', navGroup: 'Arbeiten', navOrder: 12, simpleNav: true },
  dashboard: { label: 'Dashboard', area: 'Operate', navGroup: 'Betrieb', navOrder: 15, expertOnly: true },
  help: { label: 'Hilfe', area: 'General', navGroup: 'Arbeiten', navOrder: 50, simpleNav: true },
  agents: { label: 'Agenten', area: 'Operate', navGroup: 'Betrieb', navOrder: 20, expertOnly: true },
  'worker-pool': { label: 'Worker Pool', area: 'Operate', navGroup: 'Betrieb', navOrder: 25, expertOnly: true },
  'worker-loop-diagnostics': { label: 'Worker Loop Diagnostik', area: 'Operate', navGroup: 'Betrieb', navOrder: 25.5, expertOnly: true },
  sources: { label: 'Sources', area: 'Operate', navGroup: 'Betrieb', navOrder: 26, expertOnly: true },
  'goal-artifacts': { label: 'Goal Artifacts', area: 'Operate', navGroup: 'Betrieb', navOrder: 27, expertOnly: true },
  'strategy-game-demo': { label: 'Strategy Game Demo', area: 'Operate', navGroup: 'Betrieb', navOrder: 28, expertOnly: true },
  board: { label: 'Aufgaben', area: 'Operate', navGroup: 'Arbeiten', navOrder: 20, simpleNav: true },
  operations: { label: 'Operationen', area: 'Operate', navGroup: 'Betrieb', navOrder: 40, expertOnly: true },
  artifacts: { label: 'Ergebnisse', area: 'Operate', navGroup: 'Arbeiten', navOrder: 30, simpleNav: true },
  archived: { label: 'Archiv', area: 'Operate', navGroup: 'Betrieb', navOrder: 60, expertOnly: true },
  graph: { label: 'Graph', area: 'Operate', navGroup: 'Betrieb', navOrder: 70, expertOnly: true },
  'auto-planner': { label: 'Auto-Planner', area: 'Automate', navGroup: 'Automatisierung', navOrder: 10, expertOnly: true },
  webhooks: { label: 'Webhooks', area: 'Automate', navGroup: 'Automatisierung', navOrder: 20, expertOnly: true },
  'voxtral-offline': { label: 'Voxtral Offline', area: 'Operate', navGroup: 'Arbeiten', navOrder: 45, simpleNav: true },
  'llama-runtime': { label: 'LLM Runtime', area: 'Operate', navGroup: 'Arbeiten', navOrder: 46, simpleNav: true },
  'python-runtime': { label: 'Python Runtime', area: 'System', navGroup: 'Konfiguration', navOrder: 45, expertOnly: true },
  'mobile-shell': { label: 'Mobile Shell', area: 'System', navGroup: 'Konfiguration', navOrder: 46, expertOnly: true },
  templates: { label: 'Vorlagen', area: 'Configure', navGroup: 'Arbeiten', navOrder: 40, simpleNav: true },
  'instruction-layers': { label: 'Instruction Layers', area: 'Configure', navGroup: 'Konfiguration', navOrder: 35, expertOnly: true },
  teams: { label: 'Teams', area: 'Configure', navGroup: 'Konfiguration', navOrder: 20, expertOnly: true },
  'audit-log': { label: 'Audit-Logs', area: 'System', navGroup: 'Konfiguration', navOrder: 30, adminOnly: true, expertOnly: true },
  'user-management': { label: 'Benutzerverwaltung', area: 'System', navGroup: 'Konfiguration', navOrder: 31, adminOnly: true, expertOnly: true },
  'admin-diagnostics': { label: 'Admin-Diagnose', area: 'System', navGroup: 'Konfiguration', navOrder: 32, adminOnly: true, expertOnly: true },
  'role-audit': { label: 'Rollenänderungen', area: 'System', navGroup: 'Konfiguration', navOrder: 33, adminOnly: true, expertOnly: true },
  settings: { label: 'Einstellungen', area: 'System', navGroup: 'Konfiguration', navOrder: 40, expertOnly: true },
  panel: { label: 'Agent Panel', area: 'System' },
  task: { label: 'Task Details', area: 'Operate' },
  goal: { label: 'Goal Details', area: 'Operate' },
  'context-access-policy': { label: 'Policy', area: 'Configure', navGroup: 'Konfiguration', navOrder: 36, adminOnly: true, expertOnly: true },
  'config-graph': { label: 'Konfig-Graph', area: 'Configure', navGroup: 'Konfiguration', navOrder: 37, expertOnly: true },
  'hub-worker-graph': { label: 'Hub-/Worker-Graph', area: 'Configure', navGroup: 'Konfiguration', navOrder: 37.5, expertOnly: true },
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
