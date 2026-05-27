import { Routes } from '@angular/router';

import { adminGuard } from '../../guards/admin.guard';
import { routeDataFor } from '../../models/route-metadata';

export const adminRoutes: Routes = [
  { path: 'templates', data: routeDataFor('templates'), loadComponent: () => import('../../components/templates.component').then(m => m.TemplatesComponent) },
  { path: 'teams', data: routeDataFor('teams'), loadComponent: () => import('../../components/teams.component').then(m => m.TeamsComponent) },
  { path: 'artifacts', data: routeDataFor('artifacts'), loadComponent: () => import('../../components/artifacts.component').then(m => m.ArtifactsComponent) },
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
];
