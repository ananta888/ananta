import { Routes } from '@angular/router';

/**
 * CodeHug-Routes — lazy-loaded Feature-Modul.
 *
 * Layout: <ch-shell-with-panels> als Parent, <router-outlet> rendert die
 * jeweilige Sub-View in der mittleren Spalte der Shell.
 */
export const codeHugRoutes: Routes = [
  {
    path: 'codehug',
    data: { breadcrumb: 'CodeHug', area: 'Operate' },
    loadComponent: () =>
      import('./components/codehug-shell-with-panels.component').then(m => m.CodeHugShellWithPanelsComponent),
    children: [
      { path: '', pathMatch: 'full', loadComponent: () => import('./components/codehug-dashboard.component').then(m => m.CodeHugDashboardComponent) },
      { path: 'context', data: { breadcrumb: 'Kontext-Builder', area: 'Operate' }, loadComponent: () => import('./components/codehug-context-builder.component').then(m => m.CodeHugContextBuilderComponent) },
      { path: 'search', data: { breadcrumb: 'Suche', area: 'Operate' }, loadComponent: () => import('./components/search-and-explain.component').then(m => m.SearchAndExplainComponent) },
      { path: 'refactoring', data: { breadcrumb: 'Refactoring', area: 'Operate' }, loadComponent: () => import('./components/refactoring-panel.component').then(m => m.RefactoringPanelComponent) },
      { path: 'agents', data: { breadcrumb: 'Agenten', area: 'Operate' }, loadComponent: () => import('./components/codehug-agents.component').then(m => m.CodeHugAgentsComponent) },
      { path: 'custom-agents', data: { breadcrumb: 'Custom Agents', area: 'Operate' }, loadComponent: () => import('./components/custom-agent-editor.component').then(m => m.CustomAgentEditorComponent) },
      { path: 'internals', data: { breadcrumb: 'System Internals', area: 'Operate' }, loadComponent: () => import('./components/codehug-internals.component').then(m => m.CodeHugInternalsComponent) },
      { path: 'policy', data: { breadcrumb: 'Policy', area: 'Operate' }, loadComponent: () => import('./policy-panel/policy-panel.component').then(m => m.PolicyPanelComponent) },
    ],
  },
];