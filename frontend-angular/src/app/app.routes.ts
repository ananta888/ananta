import { Routes } from '@angular/router';
import { LoginComponent } from './components/login.component';
import { NotFoundComponent } from './components/not-found.component';
import { authGuard } from './auth.guard';
import { adminRoutes } from './features/admin/admin.routes';
import { controlPlaneRoutes } from './features/control-plane/control-plane.routes';
import { systemRoutes } from './features/system/system.routes';
import { taskRoutes } from './features/tasks/task.routes';

export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
      ...controlPlaneRoutes,
      ...adminRoutes,
      ...systemRoutes,
      ...taskRoutes,
    ]
  },
  { path: '**', component: NotFoundComponent }
];
