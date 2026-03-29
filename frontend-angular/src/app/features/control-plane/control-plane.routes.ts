import { Routes } from '@angular/router';

import { AutoPlannerComponent } from '../../components/auto-planner.component';
import { DashboardComponent } from '../../components/dashboard.component';
import { OperationsConsoleComponent } from '../../components/operations-console.component';

export const controlPlaneRoutes: Routes = [
  { path: 'dashboard', component: DashboardComponent },
  { path: 'operations', component: OperationsConsoleComponent },
  { path: 'auto-planner', component: AutoPlannerComponent },
];
