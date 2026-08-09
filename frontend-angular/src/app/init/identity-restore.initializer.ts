import { APP_INITIALIZER, Injector, Provider } from '@angular/core';
import { IdentityRegistry } from '../services/identity/identity-registry';
import { NetworkProfileService } from '../services/network-profile.service';
import { isOidcPopupCallbackLocation } from '../services/oidc-popup-coordinator.service';

/**
 * Restore all identity sources (hub + oidc) from storage at app boot.
 * This lets the route guard see the right authentication state on the first navigation.
 *
 * Returns a Promise so the router waits until restore is done.
 */
export const identityRestoreInitializer: Provider = {
  provide: APP_INITIALIZER,
  multi: true,
  deps: [Injector],
  useFactory: (injector: Injector) => async () => {
    // The popup callback is a transport-only surface. Restoring identities in
    // this second window could refresh or clear the parent window's shared
    // OIDC storage before the one-time authorization code is relayed.
    if (isOidcPopupCallbackLocation()) return;
    const registry = injector.get(IdentityRegistry);
    const profiles = injector.get(NetworkProfileService);
    await registry.restoreAllFromStorage();
    await profiles.load();
  },
};
