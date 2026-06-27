import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { IdentityRegistry } from './services/identity/identity-registry';

export const authGuard: CanActivateFn = () => {
  const identities = inject(IdentityRegistry);
  const router = inject(Router);
  
  if (identities.isAuthenticated) {
    return true;
  }
  
  return router.parseUrl('/login');
};
