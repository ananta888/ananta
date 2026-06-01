import { Routes } from '@angular/router';
import { ControlCenterShellComponent } from './components/control-center-shell.component';
import { ControlCenterDashboardComponent } from './components/control-center-dashboard.component';
import { ControlCenterTaskBoardComponent } from './components/control-center-task-board.component';
import { ControlCenterSessionsComponent } from './components/control-center-sessions.component';
import { ControlCenterArtifactBrowserComponent } from './components/control-center-artifact-viewers.component';
import { ControlCenterPolicyApprovalComponent } from './components/control-center-policy-approval.component';
import { ControlCenterWorkersComponent } from './components/control-center-workers.component';
import { ControlCenterPlaceholderComponent } from './components/control-center-placeholder.component';

export const controlCenterRoutes: Routes = [{
  path: 'control-center',
  component: ControlCenterShellComponent,
  children: [
    { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
    { path: 'dashboard', component: ControlCenterDashboardComponent },
    { path: 'tasks', component: ControlCenterTaskBoardComponent },
    { path: 'sessions', component: ControlCenterSessionsComponent },
    { path: 'artifacts', component: ControlCenterArtifactBrowserComponent },
    { path: 'workers', component: ControlCenterWorkersComponent },
    { path: 'policies', component: ControlCenterPolicyApprovalComponent },
    { path: 'codecompass', component: ControlCenterPlaceholderComponent },
  ],
}];
