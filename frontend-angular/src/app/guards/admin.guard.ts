import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { PermissionService } from '../services/permission.service';

export const adminGuard: CanActivateFn = () => {
  const perm = inject(PermissionService);
  const router = inject(Router);

  if (perm.isAdmin()) {
    return true;
  }

  return router.parseUrl('/workspace');
};
