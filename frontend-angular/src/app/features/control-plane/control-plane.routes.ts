import { Routes } from '@angular/router';

import { routeDataFor } from '../../models/route-metadata';

export const controlPlaneRoutes: Routes = [
  { path: 'dashboard', data: routeDataFor('dashboard'), loadComponent: () => import('../../components/dashboard.component').then(m => m.DashboardComponent) },
  { path: 'operations', data: routeDataFor('operations'), loadComponent: () => import('../../components/operations-console.component').then(m => m.OperationsConsoleComponent) },
  { path: 'auto-planner', data: routeDataFor('auto-planner'), loadComponent: () => import('../../components/auto-planner.component').then(m => m.AutoPlannerComponent) },
];
