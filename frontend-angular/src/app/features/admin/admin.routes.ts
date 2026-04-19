import { Routes } from '@angular/router';

import { routeDataFor } from '../../models/route-metadata';

export const adminRoutes: Routes = [
  { path: 'templates', data: routeDataFor('templates'), loadComponent: () => import('../../components/templates.component').then(m => m.TemplatesComponent) },
  { path: 'teams', data: routeDataFor('teams'), loadComponent: () => import('../../components/teams.component').then(m => m.TeamsComponent) },
  { path: 'artifacts', data: routeDataFor('artifacts'), loadComponent: () => import('../../components/artifacts.component').then(m => m.ArtifactsComponent) },
];
