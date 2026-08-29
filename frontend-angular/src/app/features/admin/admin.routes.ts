import { Routes } from '@angular/router';

import { adminGuard } from '../../guards/admin.guard';
import { projectContextGuard } from '../../guards/project-context.guard';
import { sfuBroadcastOperatorGuard } from '../../guards/sfu-broadcast-operator.guard';
import { routeDataFor } from '../../models/route-metadata';

export const adminRoutes: Routes = [
  { path: 'templates', data: routeDataFor('templates'), loadComponent: () => import('../../components/templates.component').then(m => m.TemplatesComponent) },
  { path: 'teams', data: routeDataFor('teams'), loadComponent: () => import('../../components/teams.component').then(m => m.TeamsComponent) },
  { path: 'artifacts', canActivate: [projectContextGuard], data: routeDataFor('artifacts'), loadComponent: () => import('../../components/artifacts.component').then(m => m.ArtifactsComponent) },
  { path: 'knowledge', data: routeDataFor('knowledge'), loadComponent: () => import('../../components/knowledge.component').then(m => m.KnowledgeComponent) },
  { path: 'wikipedia', data: routeDataFor('wikipedia'), loadComponent: () => import('../../components/wikipedia.component').then(m => m.WikipediaComponent) },
  {
    path: 'user-management',
    canActivate: [adminGuard],
    data: routeDataFor('user-management'),
    loadComponent: () => import('../../components/user-management.component').then(m => m.UserManagementComponent),
  },
  {
    path: 'admin-diagnostics',
    canActivate: [adminGuard],
    data: routeDataFor('admin-diagnostics'),
    loadComponent: () => import('./admin-diagnostics.component').then(m => m.AdminDiagnosticsComponent),
  },
  {
    path: 'role-audit',
    canActivate: [adminGuard],
    data: routeDataFor('role-audit'),
    loadComponent: () => import('./role-audit.component').then(m => m.RoleAuditComponent),
  },
  {
    path: 'sfu-broadcast-operations',
    canActivate: [sfuBroadcastOperatorGuard],
    data: routeDataFor('sfu-broadcast-operations'),
    loadComponent: () => import('./sfu-broadcast-operator.component').then(m => m.SfuBroadcastOperatorComponent),
  },
  {
    path: 'dspy-optimization',
    canActivate: [adminGuard],
    data: routeDataFor('dspy-optimization'),
    loadComponent: () => import('./dspy-optimization-workbench.component').then(m => m.DspyOptimizationWorkbenchComponent),
  },
];
