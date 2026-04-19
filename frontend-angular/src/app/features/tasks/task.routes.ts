import { Routes } from '@angular/router';

import { routeDataFor } from '../../models/route-metadata';

export const taskRoutes: Routes = [
  { path: 'board', data: routeDataFor('board'), loadComponent: () => import('../../components/board.component').then(m => m.BoardComponent) },
  { path: 'archived', data: routeDataFor('archived'), loadComponent: () => import('../../components/archived-tasks.component').then(m => m.ArchivedTasksComponent) },
  { path: 'graph', data: routeDataFor('graph'), loadComponent: () => import('../../components/task-graph.component').then(m => m.TaskGraphComponent) },
  { path: 'task/:id', data: routeDataFor('task'), loadComponent: () => import('../../components/task-detail.component').then(m => m.TaskDetailComponent) },
  { path: 'goal/:id', data: routeDataFor('goal'), loadComponent: () => import('../../components/goal-detail.component').then(m => m.GoalDetailComponent) },
];
