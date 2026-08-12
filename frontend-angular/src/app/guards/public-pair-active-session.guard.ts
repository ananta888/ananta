import { inject } from '@angular/core';
import { CanDeactivateFn } from '@angular/router';

import type { PublicPairPageComponent } from '../features/pair/public-pair-page.component';
import { NotificationService } from '../services/notification.service';
import { ShareSessionService } from '../services/share-session.service';

/** Keeps route teardown and authoritative Public Pair retirement atomic. */
export const publicPairActiveSessionGuard: CanDeactivateFn<PublicPairPageComponent> = async () => {
  const shares = inject(ShareSessionService);
  const notifications = inject(NotificationService);
  if (shares.sessionMutationPending) {
    notifications.error(
      'Die laufende Session-Erstellung wird noch abgeschlossen. Pair Dev bleibt geöffnet.',
    );
    return false;
  }
  const active = shares.state$.value;
  if (!active.session) return true;
  if (active.role !== 'owner' && active.role !== 'participant') {
    notifications.error('Die aktive Pair-Session hat keinen gültigen lokalen Rollenbezug.');
    return false;
  }

  const prompt = active.role === 'owner'
    ? 'Beim Verlassen von Pair Dev wird die aktive Session für alle beendet. Fortfahren?'
    : 'Beim Verlassen von Pair Dev wird die aktive Teilnahme beendet. Fortfahren?';
  if (!globalThis.confirm(prompt)) return false;

  try {
    await shares.leaveSession();
    return !shares.isActive;
  } catch (error: unknown) {
    notifications.error(notifications.fromApiError(
      error,
      'Pair Dev konnte nicht sicher beendet werden. Die Seite bleibt geöffnet.',
    ));
    return false;
  }
};
