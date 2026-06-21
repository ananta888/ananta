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
      { path: 'agents', data: { breadcrumb: 'Agenten', area: 'Operate' }, loadComponent: () => import('./components/codehug-agents.component').then(m => m.CodeHugAgentsComponent) },
      { path: 'internals', data: { breadcrumb: 'System Internals', area: 'Operate' }, loadComponent: () => import('./components/codehug-internals.component').then(m => m.CodeHugInternalsComponent) },
    ],
  },
];