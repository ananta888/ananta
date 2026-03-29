import { Routes } from '@angular/router';
import { AgentsListComponent } from './components/agents-list.component';
import { AgentPanelComponent } from './components/agent-panel.component';
import { TemplatesComponent } from './components/templates.component';
import { TeamsComponent } from './components/teams.component';
import { SettingsComponent } from './components/settings.component';
import { AuditLogComponent } from './components/audit-log.component';
import { LoginComponent } from './components/login.component';
import { WebhooksComponent } from './components/webhooks.component';
import { NotFoundComponent } from './components/not-found.component';
import { ArtifactsComponent } from './components/artifacts.component';
import { authGuard } from './auth.guard';
import { controlPlaneRoutes } from './features/control-plane/control-plane.routes';
import { taskRoutes } from './features/tasks/task.routes';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
      ...controlPlaneRoutes,
      { path: 'settings', component: SettingsComponent },
      { path: 'audit-log', component: AuditLogComponent },
      { path: 'agents', component: AgentsListComponent },
      { path: 'panel/:name', component: AgentPanelComponent },
      { path: 'templates', component: TemplatesComponent },
      { path: 'teams', component: TeamsComponent },
      ...taskRoutes,
      { path: 'artifacts', component: ArtifactsComponent },
      { path: 'webhooks', component: WebhooksComponent },
    ]
  },
  { path: '**', component: NotFoundComponent }
];
