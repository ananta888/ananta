import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { UserAuthService } from './services/user-auth.service';

export const authGuard: CanActivateFn = () => {
  const auth = inject(UserAuthService);
  const router = inject(Router);
  
  if (auth.isLoggedIn()) {
    return true;
  }
  
  return router.parseUrl('/login');
};
