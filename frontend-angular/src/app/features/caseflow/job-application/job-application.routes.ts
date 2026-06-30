import { Routes } from '@angular/router';

export const JOB_APPLICATION_ROUTES: Routes = [
  {
    path: '',
    data: { breadcrumb: 'Job-Dashboard', area: 'Operate' },
    loadComponent: () =>
      import('./job-dashboard.component').then(m => m.JobDashboardComponent),
  },
  {
    path: 'board',
    data: { breadcrumb: 'Bewerbungs-Board', area: 'Operate' },
    loadComponent: () =>
      import('./application-board.component').then(m => m.ApplicationBoardComponent),
  },
  {
    path: 'discovery',
    data: { breadcrumb: 'Discovery-Inbox', area: 'Operate' },
    loadComponent: () =>
      import('./discovery-inbox.component').then(m => m.DiscoveryInboxComponent),
  },
  {
    path: 'profiles',
    data: { breadcrumb: 'Suchprofile', area: 'Operate' },
    loadComponent: () =>
      import('./search-profiles.component').then(m => m.SearchProfilesComponent),
  },
  {
    path: 'actions',
    data: { breadcrumb: 'Action-Center', area: 'Operate' },
    loadComponent: () =>
      import('./action-center.component').then(m => m.ActionCenterComponent),
  },
  {
    path: ':id',
    data: { breadcrumb: 'Bewerbung', area: 'Operate' },
    loadComponent: () =>
      import('./application-detail.component').then(m => m.ApplicationDetailComponent),
  },
];
