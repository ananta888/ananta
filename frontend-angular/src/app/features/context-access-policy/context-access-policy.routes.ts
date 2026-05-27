import { Routes } from '@angular/router';

import { adminGuard } from '../../guards/admin.guard';
import { routeDataFor } from '../../models/route-metadata';

export const contextAccessPolicyRoutes: Routes = [
  {
    path: 'context-access-policy',
    canActivate: [adminGuard],
    data: routeDataFor('context-access-policy'),
    loadComponent: () => import('./policy-overview.component').then(m => m.PolicyOverviewComponent),
  },
];
