import { Routes } from '@angular/router';

import { ArchivedTasksComponent } from '../../components/archived-tasks.component';
import { BoardComponent } from '../../components/board.component';
import { GoalDetailComponent } from '../../components/goal-detail.component';
import { TaskDetailComponent } from '../../components/task-detail.component';
import { TaskGraphComponent } from '../../components/task-graph.component';

export const taskRoutes: Routes = [
  { path: 'board', component: BoardComponent },
  { path: 'archived', component: ArchivedTasksComponent },
  { path: 'graph', component: TaskGraphComponent },
  { path: 'task/:id', component: TaskDetailComponent },
  { path: 'goal/:id', component: GoalDetailComponent },
];
