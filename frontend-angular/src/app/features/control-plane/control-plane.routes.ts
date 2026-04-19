import { Routes } from '@angular/router';

export const controlPlaneRoutes: Routes = [
  { path: 'dashboard', loadComponent: () => import('../../components/dashboard.component').then(m => m.DashboardComponent) },
  { path: 'operations', loadComponent: () => import('../../components/operations-console.component').then(m => m.OperationsConsoleComponent) },
  { path: 'auto-planner', loadComponent: () => import('../../components/auto-planner.component').then(m => m.AutoPlannerComponent) },
];
