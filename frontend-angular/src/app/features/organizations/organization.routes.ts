import { Routes } from '@angular/router';

import { projectContextGuard } from '../../guards/project-context.guard';
import { routeDataFor } from '../../models/route-metadata';

export const organizationRoutes: Routes = [
  {
    path: 'organizations',
    canActivate: [projectContextGuard],
    data: routeDataFor('organizations'),
    loadComponent: () => import('./organization-shell/organization-shell.component')
      .then(module => module.OrganizationShellComponent),
  },
];
