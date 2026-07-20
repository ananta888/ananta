import { AsyncPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Subscription } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { PeerCapabilityService } from '../../services/peer-capability.service';
import { ShareSessionService } from '../../services/share-session.service';
import { PairComputeContractPanelComponent } from './pair-compute-contract-panel.component';
import { SemanticComputeIntentFacade } from './semantic-compute-intent.facade';

@Component({
  selector: 'app-pair-compute-contract-host',
  standalone: true,
  imports: [AsyncPipe, PairComputeContractPanelComponent],
  providers: [SemanticComputeIntentFacade, PeerCapabilityService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (facade.state$ | async; as state) {
      <app-pair-compute-contract-panel
        [contract]="state.contract"
        [localMeasurement]="state.localMeasurement"
        [peerClaim]="state.peerClaim"
        [leases]="state.leases"
        [explanation]="state.explanation"
        [suggestion]="state.suggestion"
        [pending]="state.pending"
        [errorCode]="state.errorCode"
        (intent)="facade.handleIntent($event)"
        (suggestionRequest)="facade.requestSuggestion()" />
    }
  `,
})
export class PairComputeContractHostComponent implements OnInit, OnDestroy {
  readonly facade = inject(SemanticComputeIntentFacade);
  private readonly shares = inject(ShareSessionService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly subscriptions = new Subscription();
  private contextKey = '';

  ngOnInit(): void {
    this.subscriptions.add(this.shares.state$.subscribe(() => this.bind()));
    this.bind();
  }

  ngOnDestroy(): void { this.subscriptions.unsubscribe(); }

  private bind(): void {
    const session = this.shares.state$.value.session;
    const senderId = this.shares.currentUserId;
    const epoch = session?.security_epoch ?? 0;
    const hub = this.directory.list().find(value => value.role === 'hub')
      ?? this.directory.list().find(value => value.name === 'hub');
    const hubUrl = String(hub?.url || '').trim().replace(/\/+$/, '');
    const key = session && senderId && epoch > 0 && hubUrl ? `${session.id}\x1f${epoch}\x1f${senderId}` : '';
    if (key === this.contextKey) return;
    this.contextKey = key;
    this.facade.bind(key ? {
      hubUrl,
      sessionId: session!.id,
      epoch,
      senderId,
      consentVersion: Math.max(1, session!.permissions_version ?? 1),
    } : null);
  }
}
