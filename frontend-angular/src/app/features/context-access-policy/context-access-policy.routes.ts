import { Routes } from '@angular/router';
import { routeDataFor } from '../../models/route-metadata';

export const contextAccessPolicyRoutes: Routes = [
  {
    path: 'context-access-policy',
    data: routeDataFor('context-access-policy'),
    loadComponent: () => import('./policy-overview.component').then(m => m.PolicyOverviewComponent)
  }
];
