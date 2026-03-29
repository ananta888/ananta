import { Routes } from '@angular/router';

import { AgentPanelComponent } from '../../components/agent-panel.component';
import { AgentsListComponent } from '../../components/agents-list.component';
import { AuditLogComponent } from '../../components/audit-log.component';
import { SettingsComponent } from '../../components/settings.component';
import { WebhooksComponent } from '../../components/webhooks.component';

export const systemRoutes: Routes = [
  { path: 'settings', component: SettingsComponent },
  { path: 'audit-log', component: AuditLogComponent },
  { path: 'agents', component: AgentsListComponent },
  { path: 'panel/:name', component: AgentPanelComponent },
  { path: 'webhooks', component: WebhooksComponent },
];
