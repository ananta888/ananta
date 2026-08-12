import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';
import { PairSessionBindingStore } from '../services/pair-session-binding.store';
import { NotificationService } from '../services/notification.service';
import { ShareSessionService } from '../services/share-session.service';

/** Allows the public Pair surface only for the pinned, current OIDC authority. */
export const publicPairGuard: CanActivateFn = () => {
  const authority = inject(PairPublicAuthorityPolicy);
  const bindings = inject(PairSessionBindingStore);
  const notifications = inject(NotificationService);
  const router = inject(Router);
  const shares = inject(ShareSessionService);

  if (!authority.ready) return router.parseUrl('/login?sphere=oidc');
  if (shares.sessionMutationPending) {
    notifications.error('Eine laufende Session-Erstellung muss vor Public Pair abgeschlossen werden.');
    return false;
  }
  const activeSessionId = shares.state$.value.session?.id ?? '';
  if (activeSessionId) {
    const binding = bindings.get(activeSessionId);
    if (binding?.kind !== 'public') {
      notifications.error('Eine aktive Hub-Share-Session muss vor Public Pair beendet werden.');
      return false;
    }
    try {
      authority.require(binding);
    } catch {
      notifications.error('Die aktive Public-Pair-Session gehört zu einer anderen OIDC-Identität.');
      return false;
    }
  }
  return true;
};
