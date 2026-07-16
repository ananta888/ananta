import { Routes } from '@angular/router';

import { adminGuard } from '../../guards/admin.guard';
import { routeDataFor } from '../../models/route-metadata';

export const modelTrainingRoutes: Routes = [
  {
    path: 'model-training',
    canActivate: [adminGuard],
    data: routeDataFor('model-training'),
    loadComponent: () => import('./model-training-shell.component').then(module => module.ModelTrainingShellComponent),
  },
];
