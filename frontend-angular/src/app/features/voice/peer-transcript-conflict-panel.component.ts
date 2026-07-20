import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';

export interface PeerTranscriptCandidateView {
  candidateId: string;
  contributorLabel: string;
  sourceLabel: string;
  revision: number;
  text: string;
  verified: boolean;
}

export interface PeerTranscriptRegionView {
  regionId: string;
  kind: string;
  candidateIds: readonly string[];
  selectedCandidateId: string | null;
  unresolved: boolean;
  reasonCode: string;
}

@Component({
  selector: 'app-peer-transcript-conflict-panel',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="peer-conflict-title">
      <h2 id="peer-conflict-title">Peer-Transkriptvergleich</h2>
      <p class="binding">Resolution {{ resolutionHash }} · Policy {{ policyVersion }}</p>
      <div class="candidates">
        @for (candidate of candidates; track candidate.candidateId) {
          <article [class.unverified]="!candidate.verified">
            <h3>{{ candidate.contributorLabel }} · {{ candidate.sourceLabel }}</h3>
            <p class="meta">Revision {{ candidate.revision }} · {{ candidate.verified ? 'verifiziert' : 'quarantänisiert' }}</p>
            <blockquote>{{ candidate.text }}</blockquote>
          </article>
        }
      </div>
      <ul>
        @for (region of regions; track region.regionId) {
          <li [class.unresolved]="region.unresolved">
            <span>{{ region.kind }} · {{ region.reasonCode }}</span>
            @if (region.unresolved) { <strong>Unaufgelöst – keine stille Ergänzung</strong> }
            <div class="actions" aria-label="Nur lokale Anzeigeauswahl">
              @for (candidateId of region.candidateIds; track candidateId) {
                <button type="button" (click)="localOverride.emit({ regionId: region.regionId, candidateId })">
                  Lokal {{ candidateId }} anzeigen
                </button>
              }
            </div>
          </li>
        }
      </ul>
      <p class="notice">Lokale Anzeigeauswahlen ändern weder Evidence, Receipt noch Dataset.</p>
    </section>
  `,
  styles: [`
    :host { display: block; } .candidates { display: grid; grid-template-columns: repeat(auto-fit,minmax(16rem,1fr)); gap: .75rem; }
    article, li { border: 1px solid #506278; border-radius: .5rem; padding: .75rem; } blockquote { white-space: pre-wrap; }
    .unverified, .unresolved { border-color: #d98400; } .meta,.binding,.notice { opacity: .78; } .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  `],
})
export class PeerTranscriptConflictPanelComponent {
  @Input() candidates: readonly PeerTranscriptCandidateView[] = [];
  @Input() regions: readonly PeerTranscriptRegionView[] = [];
  @Input() resolutionHash = '';
  @Input() policyVersion = '';
  @Output() readonly localOverride = new EventEmitter<{ regionId: string; candidateId: string }>();
}
