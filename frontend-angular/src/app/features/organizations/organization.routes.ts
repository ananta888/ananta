import { Routes } from '@angular/router';

import { routeDataFor } from '../../models/route-metadata';

export const organizationRoutes: Routes = [
  {
    path: 'organizations',
    data: routeDataFor('organizations'),
    loadComponent: () => import('./organization-shell/organization-shell.component')
      .then(module => module.OrganizationShellComponent),
  },
];
