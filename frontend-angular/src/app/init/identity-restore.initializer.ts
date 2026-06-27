import { APP_INITIALIZER, Provider } from '@angular/core';
import { IdentityRegistry } from '../services/identity/identity-registry';
import { NetworkProfileService } from '../services/network-profile.service';

/**
 * Restore all identity sources (hub + oidc) from storage at app boot.
 * This lets the route guard see the right authentication state on the first navigation.
 *
 * Returns a Promise so the router waits until restore is done.
 */
export const identityRestoreInitializer: Provider = {
  provide: APP_INITIALIZER,
  multi: true,
  deps: [IdentityRegistry, NetworkProfileService],
  useFactory: (registry: IdentityRegistry, profiles: NetworkProfileService) => async () => {
    await registry.restoreAllFromStorage();
    await profiles.load();
  },
};
