import { Routes } from '@angular/router';

export const JOB_APPLICATION_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./job-dashboard.component').then(m => m.JobDashboardComponent),
  },
  {
    path: 'board',
    loadComponent: () =>
      import('./application-board.component').then(m => m.ApplicationBoardComponent),
  },
  {
    path: 'discovery',
    loadComponent: () =>
      import('./discovery-inbox.component').then(m => m.DiscoveryInboxComponent),
  },
  {
    path: 'profiles',
    loadComponent: () =>
      import('./search-profiles.component').then(m => m.SearchProfilesComponent),
  },
  {
    path: 'actions',
    loadComponent: () =>
      import('./action-center.component').then(m => m.ActionCenterComponent),
  },
  {
    path: ':id',
    loadComponent: () =>
      import('./application-detail.component').then(m => m.ApplicationDetailComponent),
  },
];
