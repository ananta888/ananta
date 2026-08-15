import { Routes } from '@angular/router';
import { JOB_APPLICATION_ROUTES } from './job-application/job-application.routes';
import { caseFlowStudioDirtyGuard } from './scenario/caseflow-studio.guard';

export const caseFlowRoutes: Routes = [
  {
    path: 'caseflow',
    data: { breadcrumb: 'CaseFlow', area: 'Operate' },
    children: [
      {
        path: '',
        pathMatch: 'full',
        loadComponent: () =>
          import('./scenario/caseflow-catalog.component').then(m => m.CaseFlowCatalogComponent),
      },
      {
        path: 'team',
        data: { breadcrumb: 'Agenten-Team', area: 'Configure' },
        loadComponent: () =>
          import('./team-builder/caseflow-team-builder.component').then(m => m.CaseFlowTeamBuilderComponent),
      },
      {
        path: 'studio',
        data: { breadcrumb: 'CaseFlow Studio', area: 'Configure' },
        canDeactivate: [caseFlowStudioDirtyGuard],
        loadComponent: () =>
          import('./scenario/caseflow-studio.component').then(m => m.CaseFlowStudioComponent),
      },
      {
        path: 'classroom',
        data: { breadcrumb: 'Classroom', area: 'Operate' },
        loadComponent: () =>
          import('../../components/classroom-assistant.component').then(m => m.ClassroomAssistantComponent),
      },
      {
        path: 'jobs',
        data: { breadcrumb: 'Job-Bewerbungen', area: 'Operate' },
        loadComponent: () =>
          import('./caseflow-shell.component').then(m => m.CaseflowShellComponent),
        children: JOB_APPLICATION_ROUTES,
      },
      {
        path: 'scenario/:scenarioId',
        data: { breadcrumb: 'Anwendungsszenario', area: 'Operate' },
        loadComponent: () =>
          import('./scenario/caseflow-runtime.component').then(m => m.CaseFlowRuntimeComponent),
      },
    ],
  },
];
