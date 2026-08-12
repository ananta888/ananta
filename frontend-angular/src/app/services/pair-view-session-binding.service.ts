import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subscription } from 'rxjs';

import { PairViewSyncService } from './pair-view-sync.service';
import { ShareSessionService } from './share-session.service';
import { SharedViewStateService } from './shared-view-state.service';

/**
 * Binds the reachable Share session lifecycle to the existing Pair-View port.
 * Session/key orchestration stays outside both the UI component and the
 * transport implementation (SRP/DIP), while the Hub remains authoritative for
 * membership, permissions and epochs.
 */
@Injectable({ providedIn: 'root' })
export class PairViewSessionBindingService implements OnDestroy {
  private readonly share = inject(ShareSessionService);
  private readonly sync = inject(PairViewSyncService);
  private readonly view = inject(SharedViewStateService);
  private readonly subscriptions = new Subscription();
  private started = false;
  private boundSessionId = '';

  start(): void {
    if (this.started) return;
    this.started = true;
    this.view.init();
    this.subscriptions.add(this.share.state$.subscribe(state => {
      const session = state.session;
      if (!session || !this.share.isStrictSession(session)) {
        this.unbind();
        return;
      }
      if (this.boundSessionId !== session.id) {
        this.sync.bindSession(session.id, this.share.currentUserId, session.security_epoch ?? 0);
        this.boundSessionId = session.id;
      } else if (session.security_epoch) {
        this.sync.updateSecurityEpoch(session.security_epoch);
      }
      if (this.share.securityState$.value.status === 'ready') this.sync.onCryptoReady();
    }));
    this.subscriptions.add(this.share.securityState$.subscribe(state => {
      if (state.status !== 'ready') return;
      const session = this.share.state$.value.session;
      if (!session || session.id !== this.boundSessionId || !session.security_epoch) return;
      this.sync.updateSecurityEpoch(session.security_epoch);
      this.sync.onCryptoReady();
    }));
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
    this.unbind();
  }

  private unbind(): void {
    if (!this.boundSessionId) return;
    this.boundSessionId = '';
    this.sync.unbindSession();
  }
}
