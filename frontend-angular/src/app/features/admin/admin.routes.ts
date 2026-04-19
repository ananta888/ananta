import { Routes } from '@angular/router';

export const adminRoutes: Routes = [
  { path: 'templates', loadComponent: () => import('../../components/templates.component').then(m => m.TemplatesComponent) },
  { path: 'teams', loadComponent: () => import('../../components/teams.component').then(m => m.TeamsComponent) },
  { path: 'artifacts', loadComponent: () => import('../../components/artifacts.component').then(m => m.ArtifactsComponent) },
];
