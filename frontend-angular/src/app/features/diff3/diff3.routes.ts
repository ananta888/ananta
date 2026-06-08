import { Routes } from '@angular/router';

export const diff3Routes: Routes = [
  {
    path: 'diff3',
    data: { breadcrumb: 'Three-Way Diff', area: 'Develop' },
    loadComponent: () =>
      import('./diff3-editor.component').then(m => m.Diff3EditorComponent),
  },
];
