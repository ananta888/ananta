import { APP_INITIALIZER, Provider } from '@angular/core';
import { AuthRequiredRouter } from '../services/auth-required-router.service';

/**
 * Welle 6: Boot the AuthRequiredRouter so 401 → /login navigation works
 * for the entire app session.
 */
export const authRequiredRouterInitializer: Provider = {
  provide: APP_INITIALIZER,
  multi: true,
  deps: [AuthRequiredRouter],
  useFactory: (router: AuthRequiredRouter) => () => router.start(),
};