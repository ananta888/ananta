import { Routes } from '@angular/router';

export const taskRoutes: Routes = [
  { path: 'board', loadComponent: () => import('../../components/board.component').then(m => m.BoardComponent) },
  { path: 'archived', loadComponent: () => import('../../components/archived-tasks.component').then(m => m.ArchivedTasksComponent) },
  { path: 'graph', loadComponent: () => import('../../components/task-graph.component').then(m => m.TaskGraphComponent) },
  { path: 'task/:id', loadComponent: () => import('../../components/task-detail.component').then(m => m.TaskDetailComponent) },
  { path: 'goal/:id', loadComponent: () => import('../../components/goal-detail.component').then(m => m.GoalDetailComponent) },
];
