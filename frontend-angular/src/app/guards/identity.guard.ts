import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { IdentityRegistry } from '../services/identity/identity-registry';
import { firstValueFrom } from 'rxjs';

/**
 * Route-Guard: allows navigation iff at least one identity-sphere is ready.
 * Otherwise redirects to /login.
 */
export const identityGuard: CanActivateFn = async () => {
  const registry = inject(IdentityRegistry);
  const router = inject(Router);

  // Wait for at least one snapshot emission (BehaviorSubject's current value
  // may be 'absent' if storage-restore hasn't completed yet).
  const isAuth = await firstValueFrom(registry.isAuthenticated$);
  if (isAuth) return true;

  // Final check on the synchronous getter (covers the case where restoreFromStorage
  // was awaited before this guard runs).
  if (registry.isAuthenticated) return true;

  return router.parseUrl('/login');
};