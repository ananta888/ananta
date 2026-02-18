import { Routes } from '@angular/router';
import { AgentsListComponent } from './components/agents-list.component';
import { AgentPanelComponent } from './components/agent-panel.component';
import { TemplatesComponent } from './components/templates.component';
import { TeamsComponent } from './components/teams.component';
import { BoardComponent } from './components/board.component';
import { TaskDetailComponent } from './components/task-detail.component';
import { DashboardComponent } from './components/dashboard.component';
import { SettingsComponent } from './components/settings.component';
import { AuditLogComponent } from './components/audit-log.component';
import { TaskGraphComponent } from './components/task-graph.component';
import { ArchivedTasksComponent } from './components/archived-tasks.component';
import { LoginComponent } from './components/login.component';
import { OperationsConsoleComponent } from './components/operations-console.component';
import { authGuard } from './auth.guard';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { 
    path: '', 
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
      { path: 'dashboard', component: DashboardComponent },
      { path: 'settings', component: SettingsComponent },
      { path: 'audit-log', component: AuditLogComponent },
      { path: 'agents', component: AgentsListComponent },
      { path: 'panel/:name', component: AgentPanelComponent },
      { path: 'templates', component: TemplatesComponent },
      { path: 'teams', component: TeamsComponent },
      { path: 'board', component: BoardComponent },
      { path: 'archived', component: ArchivedTasksComponent },
      { path: 'graph', component: TaskGraphComponent },
      { path: 'operations', component: OperationsConsoleComponent },
      { path: 'task/:id', component: TaskDetailComponent },
    ]
  },
  { path: '**', redirectTo: 'dashboard' }
];
