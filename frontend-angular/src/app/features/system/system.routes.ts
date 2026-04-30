import { Routes } from '@angular/router';

import { routeDataFor } from '../../models/route-metadata';

export const systemRoutes: Routes = [
  { path: 'voxtral-offline', data: routeDataFor('voxtral-offline'), loadComponent: () => import('../../components/voxtral-offline.component').then(m => m.VoxtralOfflineComponent) },
  { path: 'llama-runtime', data: routeDataFor('llama-runtime'), loadComponent: () => import('../../components/llama-runtime.component').then(m => m.LlamaRuntimeComponent) },
  { path: 'python-runtime', data: routeDataFor('python-runtime'), loadComponent: () => import('../../components/python-runtime.component').then(m => m.PythonRuntimeComponent) },
  { path: 'mobile-shell', data: routeDataFor('mobile-shell'), loadComponent: () => import('../../components/mobile-shell.component').then(m => m.MobileShellComponent) },
  { path: 'instruction-layers', data: routeDataFor('instruction-layers'), loadComponent: () => import('../../components/instruction-layers-workbench.component').then(m => m.InstructionLayersWorkbenchComponent) },
  { path: 'settings', data: routeDataFor('settings'), loadComponent: () => import('../../components/settings.component').then(m => m.SettingsComponent) },
  { path: 'audit-log', data: routeDataFor('audit-log'), loadComponent: () => import('../../components/audit-log.component').then(m => m.AuditLogComponent) },
  { path: 'agents', data: routeDataFor('agents'), loadComponent: () => import('../../components/agents-list.component').then(m => m.AgentsListComponent) },
  { path: 'panel/:name', data: routeDataFor('panel'), loadComponent: () => import('../../components/agent-panel.component').then(m => m.AgentPanelComponent) },
  { path: 'webhooks', data: routeDataFor('webhooks'), loadComponent: () => import('../../components/webhooks.component').then(m => m.WebhooksComponent) },
];
