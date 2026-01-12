import { Routes } from '@angular/router';
import { AgentsListComponent } from './components/agents-list.component';
import { AgentPanelComponent } from './components/agent-panel.component';
import { TemplatesComponent } from './components/templates.component';
import { BoardComponent } from './components/board.component';
import { TaskDetailComponent } from './components/task-detail.component';
import { DashboardComponent } from './components/dashboard.component';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  { path: 'dashboard', component: DashboardComponent },
  { path: 'agents', component: AgentsListComponent },
  { path: 'panel/:name', component: AgentPanelComponent },
  { path: 'templates', component: TemplatesComponent },
  { path: 'board', component: BoardComponent },
  { path: 'task/:id', component: TaskDetailComponent },
  { path: '**', redirectTo: 'dashboard' }
];
