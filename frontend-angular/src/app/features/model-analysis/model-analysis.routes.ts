import { Routes } from '@angular/router';

import { adminGuard } from '../../guards/admin.guard';
import { routeDataFor } from '../../models/route-metadata';

export const modelAnalysisRoutes: Routes = [
  {
    path: 'model-analysis',
    canActivate: [adminGuard],
    data: routeDataFor('model-analysis'),
    loadComponent: () => import('./model-analysis-shell.component')
      .then(module => module.ModelAnalysisShellComponent),
  },
];
