import { Routes } from '@angular/router';

export const systemRoutes: Routes = [
  { path: 'settings', loadComponent: () => import('../../components/settings.component').then(m => m.SettingsComponent) },
  { path: 'audit-log', loadComponent: () => import('../../components/audit-log.component').then(m => m.AuditLogComponent) },
  { path: 'agents', loadComponent: () => import('../../components/agents-list.component').then(m => m.AgentsListComponent) },
  { path: 'panel/:name', loadComponent: () => import('../../components/agent-panel.component').then(m => m.AgentPanelComponent) },
  { path: 'webhooks', loadComponent: () => import('../../components/webhooks.component').then(m => m.WebhooksComponent) },
];
