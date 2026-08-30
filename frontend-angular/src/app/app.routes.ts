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
import { codeHugRoutes } from './features/codehug/codehug.routes';
import { caseFlowRoutes } from './features/caseflow/caseflow.routes';
import { modelTrainingRoutes } from './features/model-training/model-training.routes';
import { modelAnalysisRoutes } from './features/model-analysis/model-analysis.routes';
import { PROJECT_ROUTES } from './features/projects/project.routes';
import { projectContextGuard } from './guards/project-context.guard';
import { organizationRoutes } from './features/organizations/organization.routes';
import { publicPairGuard } from './guards/public-pair.guard';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: 'register', loadComponent: () => import('./components/register.component').then(m => m.RegisterComponent) },
  { path: 'verify-email', loadComponent: () => import('./components/verify-email.component').then(m => m.VerifyEmailComponent) },
  { path: 'oidc-callback', component: OidcCallbackComponent },
  {
    path: 'pair-dev',
    canActivate: [publicPairGuard],
    loadComponent: () => import('./features/pair/public-pair-page.component')
      .then(m => m.PublicPairPageComponent),
  },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'workspace' },
      { path: 'projects', children: PROJECT_ROUTES },
      { path: 'workspace', canActivate: [projectContextGuard], data: { breadcrumb: 'Arbeitsbereich', area: 'Operate', projectScoped: true }, loadComponent: () => import('./components/personal-workspace.component').then(m => m.PersonalWorkspaceComponent) },
      { path: 'chats', data: { breadcrumb: 'AI Chats', area: 'Operate' }, loadComponent: () => import('./features/chat/chat-page.component').then(m => m.ChatPageComponent) },
      { path: 'collaboration', data: { breadcrumb: 'Collaboration', area: 'Operate' }, loadComponent: () => import('./features/collaboration/collaboration-workspace-page.component').then(m => m.CollaborationWorkspacePageComponent) },
      { path: 'classroom', pathMatch: 'full', redirectTo: 'caseflow/classroom' },
      { path: 'help', data: { breadcrumb: 'Hilfe', area: 'General' }, loadComponent: () => import('./components/help.component').then(m => m.HelpComponent) },
      { path: 'effective-workflow', data: { breadcrumb: 'Effective Workflow', area: 'Configure' }, loadComponent: () => import('./components/effective-workflow-explorer.component').then(m => m.EffectiveWorkflowExplorerComponent) },
      { path: 'config-graph', data: { breadcrumb: 'Konfig-Graph', area: 'Configure' }, loadComponent: () => import('./components/config-graph-editor.component').then(m => m.ConfigGraphEditorComponent) },
      { path: 'cli-backends', data: { breadcrumb: 'CLI-Backends', area: 'Configure' }, loadComponent: () => import('./components/cli-backend-setup.component').then(m => m.CliBackendSetupComponent) },
      { path: 'blueprint-config', data: { breadcrumb: 'Blueprint-Konfig', area: 'Configure' }, loadComponent: () => import('./components/blueprint-config-workbench.component').then(m => m.BlueprintConfigWorkbenchComponent) },
      { path: 'hub-worker-graph', data: { breadcrumb: 'Hub-/Worker-Graph', area: 'Configure' }, loadComponent: () => import('./components/hub-worker-graph-editor.component').then(m => m.HubWorkerGraphEditorComponent) },
      { path: 'knowledge-hygiene/:projectId', canActivate: [projectContextGuard], data: { breadcrumb: 'Knowledge Hygiene', area: 'Operate', projectScoped: true }, loadComponent: () => import('./features/knowledge-hygiene/knowledge-hygiene-page.component').then(m => m.KnowledgeHygienePageComponent) },
      { path: 'markdown-slides', data: { breadcrumb: 'Markdown Slides', area: 'Operate' }, loadComponent: () => import('./features/markdown-slides/markdown-slides.component').then(m => m.MarkdownSlidesComponent) },
      ...controlPlaneRoutes,
      ...controlCenterRoutes,
      ...adminRoutes,
      ...systemRoutes,
      ...taskRoutes,
      ...contextAccessPolicyRoutes,
      ...modelTrainingRoutes,
      ...modelAnalysisRoutes,
      ...organizationRoutes,
      ...visualProcessRoutes,
      ...diff3Routes,
      ...codeHugRoutes,
      ...caseFlowRoutes,
    ]
  },
  { path: '**', component: NotFoundComponent }
];
