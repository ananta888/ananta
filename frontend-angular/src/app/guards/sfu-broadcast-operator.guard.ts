import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { UserAuthService } from '../services/user-auth.service';

export const sfuBroadcastOperatorGuard: CanActivateFn = () => {
  const auth = inject(UserAuthService);
  const router = inject(Router);
  const role = String(auth.userPayload?.role || '').toLowerCase();
  return role === 'admin' || role === 'operator' ? true : router.parseUrl('/workspace');
};
