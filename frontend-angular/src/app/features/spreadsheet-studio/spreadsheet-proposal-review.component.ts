import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { SpreadsheetDocument, SpreadsheetProposalResult } from './spreadsheet-studio.models';

@Component({
  selector: 'app-spreadsheet-proposal-review',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (proposal) {
      <section class="review" aria-labelledby="proposal-review-title">
        <h3 id="proposal-review-title">Candidate, Diff und Validatoren</h3>
        <dl>
          <dt>Status</dt><dd>{{ proposal.state }}</dd>
          <dt>Action-Digest</dt><dd><code>{{ proposal.proposal_digest }}</code></dd>
          <dt>Basis</dt><dd>v{{ proposal.base_version }} · <code>{{ proposal.base_snapshot_digest }}</code></dd>
          <dt>Candidate</dt><dd><code>{{ proposal.candidate_snapshot_digest }}</code></dd>
          <dt>Diff-Digest</dt><dd><code>{{ proposal.actual_diff.diff_digest }}</code></dd>
          <dt>Änderungen</dt><dd>{{ proposal.actual_diff.total }} gesamt; {{ indirectCount() }} indirekt</dd>
          <dt>Validatoren</dt><dd>{{ proposal.validation.passed ? 'bestanden' : 'blockiert' }} · {{ proposal.validation.results.length }} Ergebnisse</dd>
          <dt>Risiken</dt><dd>{{ proposal.reason_codes.join(', ') || 'keine gemeldeten Risiken' }}</dd>
        </dl>
        @if (unsupportedObjects.length) {
          <p class="warning">Nicht unterstützte Objekte: {{ unsupportedObjects.join(', ') }}</p>
        }
        <div class="diff" role="table" aria-label="Action-Diff">
          @for (item of proposal.actual_diff.items; track item.object_id) {
            <div role="row" [class.indirect]="!item.direct">
              <strong role="cell">{{ item.sheet_id || item.kind }}{{ item.cell ? '!' + item.cell : '' }}</strong>
              <span role="cell">{{ item.direct ? 'direkt' : 'indirekt' }}</span>
              <span role="cell">{{ item.change_kinds.join(', ') }}</span>
              <code role="cell">{{ item.before | json }} → {{ item.after | json }}</code>
            </div>
          }
        </div>
        @if (proposal.actual_diff.has_more) {
          <button type="button" class="secondary" (click)="loadMore.emit()">Weitere Diff-Seite laden</button>
        }
        @if (!bindingCurrent()) {
          <p class="error" role="alert">Die aktive Dokumentversion stimmt nicht mehr mit dem Candidate überein. Apply ist gesperrt.</p>
        }
        <div class="actions">
          <button type="button" (click)="apply.emit()" [disabled]="!canApply()">Digestgebunden anwenden</button>
          <button type="button" class="secondary" (click)="edit.emit()">Bearbeiten / neu vorschlagen</button>
          <button type="button" class="secondary" (click)="reject.emit()">Candidate verwerfen</button>
        </div>
      </section>
    }
  `,
  styles: [`
    .review { margin-top:14px; border-top:1px solid var(--border-color, #778); padding-top:10px; }
    dl { display:grid; grid-template-columns:max-content 1fr; gap:5px 12px; }
    dt { font-weight:700; } dd { margin:0; overflow-wrap:anywhere; }
    .diff { max-height:320px; overflow:auto; border:1px solid var(--border-color, #778); }
    .diff>div { display:grid; grid-template-columns:minmax(100px,.6fr) auto minmax(100px,.7fr) minmax(180px,1.5fr); gap:8px; padding:7px; border-bottom:1px solid var(--border-color, #dde); }
    .diff .indirect { border-left:4px solid var(--warning, #b7791f); }
    code { overflow-wrap:anywhere; }
    .warning { border-left:4px solid var(--warning, #b7791f); padding:8px; }
    .error { color:var(--danger, #b42318); }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
    @media(max-width:700px){.diff>div{grid-template-columns:1fr}.diff code{white-space:pre-wrap}}
  `],
})
export class SpreadsheetProposalReviewComponent {
  @Input({ required: true }) proposal: SpreadsheetProposalResult | null = null;
  @Input({ required: true }) document: SpreadsheetDocument | null = null;
  @Input() synchronized = false;
  @Input() unsupportedObjects: string[] = [];
  @Output() apply = new EventEmitter<void>();
  @Output() edit = new EventEmitter<void>();
  @Output() reject = new EventEmitter<void>();
  @Output() loadMore = new EventEmitter<void>();

  indirectCount(): number { return this.proposal?.actual_diff.items.filter(item => !item.direct).length || 0; }

  bindingCurrent(): boolean {
    return Boolean(this.proposal && this.document
      && this.proposal.document_id === this.document.document_id
      && this.proposal.base_version === this.document.version
      && this.proposal.base_snapshot_digest === this.document.snapshot_digest);
  }

  canApply(): boolean {
    return Boolean(this.proposal?.state === 'candidate_ready'
      && this.proposal.validation.passed
      && this.synchronized
      && this.bindingCurrent()
      && this.unsupportedObjects.length === 0);
  }
}
