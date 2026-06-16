import { Routes } from '@angular/router';
import { LoginComponent } from './components/login.component';
import { OidcCallbackComponent } from './components/oidc-callback.component';
import { NotFoundComponent } from './components/not-found.component';
import { authGuard } from './auth.guard';
import { adminRoutes } from './features/admin/admin.routes';
import { controlPlaneRoutes } from './features/control-plane/control-plane.routes';
import { controlCenterRoutes } from './features/control-center/control-center.routes';
import { systemRoutes } from './features/system/system.routes';
import { taskRoutes } from './features/tasks/task.routes';
import { contextAccessPolicyRoutes } from './features/context-access-policy/context-access-policy.routes';
import { visualProcessRoutes } from './features/visual-process/visual-process.routes';
import { diff3Routes } from './features/diff3/diff3.routes';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'oidc-callback', component: OidcCallbackComponent },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'workspace' },
      { path: 'workspace', data: { breadcrumb: 'Arbeitsbereich', area: 'Operate' }, loadComponent: () => import('./components/personal-workspace.component').then(m => m.PersonalWorkspaceComponent) },
      { path: 'chats', data: { breadcrumb: 'AI Chats', area: 'Operate' }, loadComponent: () => import('./features/chat/chat-page.component').then(m => m.ChatPageComponent) },
      { path: 'help', data: { breadcrumb: 'Hilfe', area: 'General' }, loadComponent: () => import('./components/help.component').then(m => m.HelpComponent) },
      { path: 'config-graph', data: { breadcrumb: 'Konfig-Graph', area: 'Configure' }, loadComponent: () => import('./components/config-graph-editor.component').then(m => m.ConfigGraphEditorComponent) },
      ...controlPlaneRoutes,
      ...controlCenterRoutes,
      ...adminRoutes,
      ...systemRoutes,
      ...taskRoutes,
      ...contextAccessPolicyRoutes,
      ...visualProcessRoutes,
      ...diff3Routes,
    ]
  },
  { path: '**', component: NotFoundComponent }
];
