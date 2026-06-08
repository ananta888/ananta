import { Routes } from '@angular/router';

export const visualProcessRoutes: Routes = [
  {
    path: 'process-designer',
    data: { breadcrumb: 'Prozess-Designer', area: 'Build' },
    loadComponent: () =>
      import('./visual-process-editor.component').then(
        m => m.VisualProcessEditorComponent,
      ),
  },
];
