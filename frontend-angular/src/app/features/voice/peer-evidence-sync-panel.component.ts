import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { SpeechEvidenceGroupPreview } from '../../services/speech-evidence-sync.validators';

import {
  peerEvidenceAcceptEnabled,
  peerEvidenceBulkAcceptForbidden,
} from './peer-evidence-acceptance';
import {
  PeerTranscriptCandidateView,
  PeerTranscriptConflictPanelComponent,
  PeerTranscriptRegionView,
} from './peer-transcript-conflict-panel.component';
import { SpeechDatasetPrivacyPreviewComponent } from '../ml-intern/speech-dataset-privacy-preview.component';
import {
  SpeechDatasetLineageComponent,
  SpeechDatasetLineageNodeView,
} from '../ml-intern/speech-dataset-lineage.component';

export interface PeerEvidenceOfferView {
  readonly offerId: string;
  readonly direction: string;
  readonly purpose: string;
  readonly dataClasses: readonly string[];
  readonly fields: readonly string[];
  readonly retentionSeconds: number;
  readonly trainerClass: string;
  readonly groupCount: number;
  readonly groupPreviews: readonly SpeechEvidenceGroupPreview[];
  readonly previewVerified: boolean;
  readonly totalBytes: number;
  readonly senderConsentVersion: number;
  readonly recipientConsentVersion: number;
  readonly state: string;
  readonly action: 'accept' | 'awaiting_peer' | 'transfer' | 'terminal';
  readonly expiresAtMs: number;
}

export interface PeerEvidenceLocalGroupView {
  readonly groupId: string;
  readonly turnId: string;
  readonly revision: number;
  readonly dataClass: 'transcript' | 'text_corrections';
  readonly fields: readonly string[];
  readonly byteLength: number;
  readonly sourceState: string;
}

export interface PeerEvidenceQuarantineView {
  readonly offerId: string;
  readonly groupId: string;
  readonly receivedChunks: number;
  readonly chunkCount: number;
  readonly firstMissingIndex: number;
  readonly receivedBytes: number;
  readonly state: 'receiving' | 'quarantined' | 'conflict' | 'revoked';
  readonly reasonCode: string | null;
}

export interface PeerEvidenceLineageView {
  readonly groupId: string;
  readonly contributorDigest: string;
  readonly consentDigest: string;
  readonly fieldProvenanceDigests: readonly string[];
  readonly state: 'quarantined' | 'accepted' | 'rejected' | 'revoked';
}

export interface PeerEvidenceSyncView {
  readonly state: string;
  readonly pending: boolean;
  readonly acknowledgedChunks: number;
  readonly chunkCount: number;
  readonly firstMissingIndex: number;
  readonly inFlightBytes: number;
  readonly retries: number;
  readonly quarantineCount: number;
  readonly receiptId: string | null;
  readonly receiptVerification: 'none' | 'peer_verified' | 'hub_verified';
  readonly curationTaskId: string | null;
  readonly datasetId: string | null;
  readonly datasetManifestDigest: string | null;
  readonly datasetLineageNodes: readonly SpeechDatasetLineageNodeView[];
  readonly revocationState: string | null;
  readonly reasonCode: string | null;
  readonly localGroups: readonly PeerEvidenceLocalGroupView[];
  readonly quarantine: readonly PeerEvidenceQuarantineView[];
  readonly lineage: readonly PeerEvidenceLineageView[];
  readonly candidates: readonly PeerTranscriptCandidateView[];
  readonly regions: readonly PeerTranscriptRegionView[];
  readonly resolutionHash: string;
  readonly resolutionPolicyVersion: string;
}

export interface PeerEvidenceProposalIntent {
  readonly groupIds: readonly string[];
  readonly trainerClass: 'none' | 'speech_adaptation';
}

@Component({
  selector: 'app-peer-evidence-sync-panel',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    PeerTranscriptConflictPanelComponent,
    SpeechDatasetPrivacyPreviewComponent,
    SpeechDatasetLineageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="peer-sync-title">
      <h2 id="peer-sync-title">Peer-Evidence-Synchronisierung</h2>
      <p role="status" aria-live="polite">{{ availableReason }}</p>

      @if (sync?.localGroups?.length) {
        <fieldset data-testid="peer-evidence-local-groups">
          <legend>Consent-gefilterte lokale Evidence anbieten</legend>
          @for (group of sync!.localGroups; track group.groupId) {
            <label>
              <input type="checkbox" [checked]="selectedLocal.has(group.groupId)"
                [disabled]="sync!.pending" (change)="toggleLocal(group.groupId, $any($event.target).checked)" />
              {{ group.turnId }} · Revision {{ group.revision }} · {{ group.dataClass }} · {{ group.byteLength }} Bytes
            </label>
          }
          <label>Trainerklasse
            <select [(ngModel)]="trainerClass" [disabled]="sync!.pending">
              <option value="none">Keine Dataset-/Trainingsfreigabe</option>
              <option value="speech_adaptation">Speech-Adaptation (nur bei explizitem Consent)</option>
            </select>
          </label>
          <button type="button" [disabled]="!canPropose()" (click)="proposeSelected()">
            Ausgewählte Evidence anbieten
          </button>
          <p role="note">Es werden nur die ausgewählten Gruppen übertragen; Keys und Inhalte erscheinen in keinem Debugstatus.</p>
        </fieldset>
      }

      @if (offer) {
        <dl data-testid="peer-evidence-offer">
          <dt>Richtung</dt><dd>{{ offer.direction }}</dd>
          <dt>Zweck</dt><dd>{{ offer.purpose }}</dd>
          <dt>Datenklassen</dt><dd>{{ offer.dataClasses.join(', ') }}</dd>
          <dt>Felder</dt><dd>{{ offer.fields.join(', ') }}</dd>
          <dt>Retention</dt><dd>{{ offer.retentionSeconds }} Sekunden</dd>
          <dt>Trainerklasse</dt><dd>{{ offer.trainerClass }}</dd>
          <dt>Umfang</dt><dd>{{ offer.groupCount }} Gruppen · {{ offer.totalBytes }} Bytes</dd>
          <dt>Signierte Gruppenpreview</dt>
          <dd>
            {{ offer.previewVerified ? 'Scope kryptografisch und semantisch geprüft' : 'nicht geprüft' }}
            <ul data-testid="peer-evidence-group-preview">
              @for (preview of offer.groupPreviews; track preview.groupId) {
                <li>{{ preview.groupId }} · Revision {{ preview.revision }} · {{ preview.sizeBytes }} Bytes
                  · {{ preview.qualityBasis }} · Speaker {{ preview.speakerScopeDigest.slice(0, 12) }}…
                  · Source {{ preview.sourceGroupDigest.slice(0, 12) }}…
                  <div data-testid="peer-evidence-comparison-preview">
                    <strong>Inhaltsfreie Originalkandidaten</strong>
                    <ul>
                      @for (candidate of preview.originalCandidates; track candidate.candidateDigest) {
                        <li>Original {{ candidate.ordinal }} · Revision {{ candidate.revision }}
                          · Kandidat {{ candidate.candidateDigest.slice(0, 12) }}…
                          · Autorität {{ candidate.authorityDigest.slice(0, 12) }}…</li>
                      }
                    </ul>
                    <span>Resolution: {{ preview.resolutionState }}</span>
                    @if (preview.selectedCandidateDigest) {
                      <span> · gewählt {{ preview.selectedCandidateDigest.slice(0, 12) }}…</span>
                    }
                    @if (preview.unresolvedRegionDigests.length) {
                      <span> · unaufgelöste Regionen {{ preview.unresolvedRegionDigests.length }}</span>
                    }
                    <span> · Bindung {{ preview.comparisonDigest.slice(0, 12) }}…</span>
                    <p role="note">Vor Zustimmung werden weder Rohtext noch Audio oder Features offengelegt.</p>
                  </div>
                </li>
              }
            </ul>
          </dd>
          <dt>Consent</dt><dd>Sender v{{ offer.senderConsentVersion }} · Empfänger v{{ offer.recipientConsentVersion }}</dd>
          <dt>Status</dt><dd>{{ offer.state }}</dd>
        </dl>
        @if (offer.action === 'accept') {
          <fieldset>
            <legend>Scope einzeln bestätigen</legend>
            @for (dataClass of offer.dataClasses; track dataClass) {
              <label>
                <input type="checkbox" [disabled]="forbiddenBulk(dataClass) || sync?.pending"
                  [checked]="selectedRemote.has(dataClass)"
                  (change)="toggleRemote(dataClass, $any($event.target).checked)" />
                {{ dataClass }} {{ forbiddenBulk(dataClass) ? '– kein Bulk-Accept' : '' }}
              </label>
            }
          </fieldset>
        }
        <div class="actions">
          @if (offer.action === 'accept') {
            <button type="button" [disabled]="!canAccept()" (click)="accept.emit([...selectedRemote])">
              Auswahl zustimmen
            </button>
            <button type="button" [disabled]="sync?.pending" (click)="reject.emit()">Ablehnen</button>
          }
          <button type="button" [disabled]="sync?.pending" (click)="pauseRequested.emit()">Pausieren</button>
          <button type="button" [disabled]="sync?.pending" (click)="resumeRequested.emit()">Fortsetzen</button>
          <button type="button" [disabled]="sync?.pending" (click)="revoke.emit()">Widerrufen</button>
          @if (offer.trainerClass === 'speech_adaptation') {
            <button type="button" [disabled]="!canRequestCuration()" (click)="curationRequested.emit()">
              Hub-Kuratierung und Dataset-Aufnahme anfordern
            </button>
          }
        </div>
      }

      @if (sync) {
        <p data-testid="peer-evidence-transfer-status">
          Status {{ sync.state }} · ACK {{ sync.acknowledgedChunks }}/{{ sync.chunkCount }} · Cursor {{ sync.firstMissingIndex }}
        </p>
        <p>In-flight {{ sync.inFlightBytes }} Bytes · Retries {{ sync.retries }} · Quarantäne {{ sync.quarantineCount }}</p>
        @if (sync.reasonCode) { <p role="alert">{{ sync.reasonCode }}</p> }
        @if (sync.receiptId) {
          <p>Receipt {{ sync.receiptId }} · {{ sync.receiptVerification }}</p>
        }
        @if (sync.curationTaskId) {
          <p>Hub-Curation-Task {{ sync.curationTaskId }}</p>
        }
        @if (sync.datasetId) {
          <p>Dataset {{ sync.datasetId }}
            @if (sync.datasetManifestDigest) { · Manifest {{ sync.datasetManifestDigest }} }
          </p>
        }
        @if (sync.datasetManifestDigest) {
          <app-speech-dataset-privacy-preview
            [hubUrl]="hubUrl"
            [manifestDigest]="sync.datasetManifestDigest" />
        }
        @if (sync.datasetLineageNodes.length) {
          <app-speech-dataset-lineage [nodes]="sync.datasetLineageNodes" />
        }
        @if (sync.revocationState === 'unresolved') {
          <p role="alert">Remote-Widerruf unaufgelöst; keine Löschung wird behauptet.</p>
        } @else if (sync.revocationState === 'acknowledged') {
          <p>Remote-Ack signiert empfangen; dies ist keine technische Garantie für ein untrusted Peer-System.</p>
        }

        @if (sync.quarantine.length) {
          <h3>Verschlüsselte lokale Quarantäne</h3>
          <ul data-testid="peer-evidence-quarantine">
            @for (row of sync.quarantine; track row.offerId + ':' + row.groupId) {
              <li>
                {{ row.groupId }} · {{ row.state }} · {{ row.receivedChunks }}/{{ row.chunkCount }} Chunks
                · Cursor {{ row.firstMissingIndex }} · {{ row.receivedBytes }} Bytes
                @if (row.reasonCode) { · {{ row.reasonCode }} }
              </li>
            }
          </ul>
        }

        @if (sync.lineage.length) {
          <h3>Evidence-Lineage (content-free)</h3>
          <ul data-testid="peer-evidence-lineage">
            @for (row of sync.lineage; track row.groupId) {
              <li>{{ row.groupId }} · {{ row.state }} · Contributor {{ row.contributorDigest }}
                · Consent {{ row.consentDigest }} · Felder {{ row.fieldProvenanceDigests.join(', ') }}</li>
            }
          </ul>
        }

        @if (sync.candidates.length || sync.regions.length) {
          <app-peer-transcript-conflict-panel
            [candidates]="sync.candidates"
            [regions]="sync.regions"
            [resolutionHash]="sync.resolutionHash"
            [policyVersion]="sync.resolutionPolicyVersion"
            (localOverride)="localOverride.emit($event)" />
        }
      }
    </section>
  `,
  styles: [`
    :host { display:block; }
    dl { display:grid; grid-template-columns:max-content 1fr; gap:.35rem .75rem; }
    dt { font-weight:600; }
    fieldset { display:grid; gap:.4rem; margin:1rem 0; }
    .actions { display:flex; gap:.5rem; flex-wrap:wrap; }
    button, select, input { min-height: 2.75rem; }
    li, dd { overflow-wrap:anywhere; }
  `],
})
export class PeerEvidenceSyncPanelComponent implements OnChanges {
  @Input() hubUrl = '';
  @Input() offer: PeerEvidenceOfferView | null = null;
  @Input() sync: PeerEvidenceSyncView | null = null;
  @Input() availableReason = 'Noch kein Hub-autorisierter Evidence-Offer vorhanden.';
  @Output() readonly propose = new EventEmitter<PeerEvidenceProposalIntent>();
  @Output() readonly accept = new EventEmitter<readonly string[]>();
  @Output() readonly pauseRequested = new EventEmitter<void>();
  @Output() readonly resumeRequested = new EventEmitter<void>();
  @Output() readonly reject = new EventEmitter<void>();
  @Output() readonly revoke = new EventEmitter<void>();
  @Output() readonly curationRequested = new EventEmitter<void>();
  @Output() readonly localOverride = new EventEmitter<{ regionId: string; candidateId: string }>();
  readonly selectedLocal = new Set<string>();
  readonly selectedRemote = new Set<string>();
  trainerClass: PeerEvidenceProposalIntent['trainerClass'] = 'none';

  ngOnChanges(): void {
    const local = new Set(this.sync?.localGroups.map(value => value.groupId) ?? []);
    for (const value of [...this.selectedLocal]) if (!local.has(value)) this.selectedLocal.delete(value);
    for (const value of [...this.selectedRemote]) {
      if (!this.offer?.dataClasses.includes(value) || this.forbiddenBulk(value)) this.selectedRemote.delete(value);
    }
  }

  forbiddenBulk(value: string): boolean {
    return peerEvidenceBulkAcceptForbidden(value);
  }

  toggleLocal(value: string, checked: boolean): void {
    if (!this.sync?.localGroups.some(group => group.groupId === value)) return;
    if (checked) this.selectedLocal.add(value); else this.selectedLocal.delete(value);
  }

  toggleRemote(value: string, checked: boolean): void {
    if (this.forbiddenBulk(value)) return;
    if (checked) this.selectedRemote.add(value); else this.selectedRemote.delete(value);
  }

  canPropose(): boolean { return !this.sync?.pending && this.selectedLocal.size > 0; }
  canAccept(): boolean {
    return peerEvidenceAcceptEnabled(
      this.offer,
      this.sync?.pending === true,
      [...this.selectedRemote],
    );
  }

  canRequestCuration(): boolean {
    return this.offer?.trainerClass === 'speech_adaptation'
      && this.offer.action === 'transfer'
      && !this.sync?.pending
      && this.sync?.receiptVerification !== 'hub_verified'
      && this.sync?.quarantine.length === this.offer.groupCount
      && this.sync.quarantine.every(value => value.state === 'quarantined');
  }

  proposeSelected(): void {
    if (!this.canPropose()) return;
    this.propose.emit(Object.freeze({ groupIds: Object.freeze([...this.selectedLocal]), trainerClass: this.trainerClass }));
  }
}
