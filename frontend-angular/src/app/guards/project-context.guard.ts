import { inject } from '@angular/core';
import type { CanActivateFn } from '@angular/router';
import { Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';

import { ProjectContextService } from '../services/project-context.service';

export const projectContextGuard: CanActivateFn = (_route, state) => {
  const context = inject(ProjectContextService);
  const router = inject(Router);
  return context.ensureLoaded().pipe(
    map(() => context.hasProject()
      ? true
      : router.createUrlTree(['/projects'], {
          queryParams: { returnUrl: state.url },
        })),
    catchError(() => of(router.createUrlTree(['/projects'], {
      queryParams: { returnUrl: state.url },
    }))),
  );
};
