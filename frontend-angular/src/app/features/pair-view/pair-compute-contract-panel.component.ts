import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

export type ComputeProfile = 'off' | 'conservative' | 'balanced' | 'custom';

export interface ComputeContractView {
  contractId: string;
  digest?: string;
  revision: number;
  status: 'absent' | 'offered' | 'countered' | 'accepted' | 'active' | 'revoked' | 'fallback';
  profile: ComputeProfile;
  qualityLevel?: 'best_effort' | 'standard' | 'verified';
  delayMs: number;
  expiresAtMs?: number;
  reasonCode?: string;
  roles: { primary?: string[]; validator?: string[]; standby?: string[] };
}

export interface CapabilityView {
  cpu: string;
  memory: string;
  gpu: string;
  codec: string;
  battery: string;
  network: string;
  expiresAtMs?: number;
}

export interface ComputeLeaseView {
  leaseId: string;
  contractDigest?: string;
  role: 'primary' | 'validator' | 'standby';
  executorId: string;
  status: 'active' | 'expired' | 'revoked';
  expiresAtMs: number;
  deadlineAtMs?: number;
  resourceBudget?: { cpuMs: number; memoryBytes: number; artifactBytes: number };
}

export interface ComputeExplanationView {
  message: string;
  reasonCode: string;
  authoritativeSource: 'hub';
}

export interface ComputeSuggestionView {
  profile?: ComputeProfile;
  delayMs?: number;
  rationale: string;
  authoritative: false;
  requiresSeparateHubMutation: true;
}

export interface ComputeContractIntent {
  kind: 'profile' | 'delay' | 'accept' | 'activate' | 'revoke' | 'reduce' | 'fallback';
  expectedRevision: number;
  value?: ComputeProfile | number;
}

const EMPTY_CONTRACT: ComputeContractView = {
  contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {},
};

@Component({
  selector: 'app-pair-compute-contract-panel',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './pair-compute-contract-panel.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    :host { display: block; }
    .compute-panel { border: 1px solid #3b3d46; border-radius: .6rem; padding: .8rem; margin-top: .75rem; }
    header, .actions { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
    h4 { margin: 0; flex: 1; }
    .status { border-radius: 999px; padding: .15rem .45rem; background: #353842; }
    .status.active { background: #246b3d; }
    .stale { color: #ffb4a8; font-weight: 600; }
    .sources { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .5rem; margin: .7rem 0; }
    .source { border-inline-start: .3rem solid #687078; padding: .45rem; background: #25272d; }
    .source.local { border-color: #3b82f6; } .source.peer { border-color: #a855f7; }
    .source.hub { border-color: #22c55e; } .source.lease { border-color: #f59e0b; }
    dt { color: #adb1bd; font-size: .78rem; } dd { margin: 0 0 .3rem; }
    label { display: flex; flex-direction: column; gap: .2rem; }
    button, select, input { font: inherit; }
    button { min-height: 2.4rem; }
    .pending { color: #facc15; }
  `],
})
export class PairComputeContractPanelComponent implements OnChanges {
  @Input() contract: ComputeContractView = EMPTY_CONTRACT;
  @Input() localMeasurement?: CapabilityView;
  @Input() peerClaim?: CapabilityView;
  @Input() leases: readonly ComputeLeaseView[] = [];
  @Input() explanation?: ComputeExplanationView;
  @Input() suggestion?: ComputeSuggestionView;
  @Input() nowMs?: number;
  @Input() pending = false;
  @Input() errorCode: string | null = null;
  @Output() readonly intent = new EventEmitter<ComputeContractIntent>();
  @Output() readonly suggestionRequest = new EventEmitter<void>();

  selectedProfile: ComputeProfile = 'off';
  selectedDelayMs = 5_000;

  ngOnChanges(): void {
    if (!this.pending) {
      this.selectedProfile = this.contract.profile;
      this.selectedDelayMs = this.contract.delayMs;
    }
  }

  get authoritativeActive(): boolean {
    return this.contract.status === 'active' && !this.contractStale;
  }

  get contractStale(): boolean {
    return this.contract.expiresAtMs !== undefined && this.contract.expiresAtMs <= this.effectiveNowMs();
  }

  leaseActive(lease: ComputeLeaseView): boolean {
    const digestMatches = !this.contract.digest || lease.contractDigest === this.contract.digest;
    return lease.status === 'active'
      && lease.expiresAtMs > this.effectiveNowMs()
      && this.authoritativeActive
      && digestMatches;
  }

  leaseStatus(lease: ComputeLeaseView): string {
    if (this.leaseActive(lease)) return 'aktiv';
    if (lease.status === 'active' && this.contract.digest && lease.contractDigest !== this.contract.digest) {
      return 'veraltet';
    }
    if (lease.status === 'active' && lease.expiresAtMs <= this.effectiveNowMs()) return 'abgelaufen';
    if (lease.status === 'active') return 'nicht autoritativ';
    return lease.status;
  }

  remainingSeconds(expiresAtMs: number | undefined): number | null {
    if (expiresAtMs === undefined) return null;
    return Math.max(0, Math.ceil((expiresAtMs - this.effectiveNowMs()) / 1_000));
  }

  emitProfile(): void {
    if (!['off', 'conservative', 'balanced', 'custom'].includes(this.selectedProfile)) return;
    this.emit('profile', this.selectedProfile);
  }

  emitDelay(): void {
    if (!Number.isInteger(this.selectedDelayMs) || this.selectedDelayMs < 2_000 || this.selectedDelayMs > 20_000) return;
    this.emit('delay', this.selectedDelayMs);
  }

  emit(
    kind: 'accept' | 'activate' | 'revoke' | 'reduce' | 'fallback' | 'profile' | 'delay',
    value?: ComputeProfile | number,
  ): void {
    if (this.pending || (this.contract.revision < 1 && !['profile', 'delay'].includes(kind))) return;
    this.intent.emit({ kind, expectedRevision: this.contract.revision, value });
  }

  canAccept(): boolean { return !this.pending && ['offered', 'countered'].includes(this.contract.status); }
  canActivate(): boolean { return !this.pending && this.contract.status === 'accepted'; }
  canMutateExisting(): boolean { return !this.pending && this.contract.revision > 0; }
  canReduceProfile(): boolean { return this.canMutateExisting() && this.contract.profile !== 'off'; }

  requestSuggestion(): void {
    if (!this.pending && this.contract.revision > 0) this.suggestionRequest.emit();
  }

  applySuggestedProfile(): void {
    if (this.suggestion?.requiresSeparateHubMutation && this.suggestion.profile) {
      this.emit('profile', this.suggestion.profile);
    }
  }

  applySuggestedDelay(): void {
    if (this.suggestion?.requiresSeparateHubMutation && this.suggestion.delayMs !== undefined) {
      this.emit('delay', this.suggestion.delayMs);
    }
  }

  private effectiveNowMs(): number { return this.nowMs ?? Date.now(); }
}
