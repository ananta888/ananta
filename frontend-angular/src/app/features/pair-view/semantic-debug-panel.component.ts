import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from "@angular/core";

export interface SemanticDebugEventView {
  event_id: string;
  scope_digest: string;
  event_type: string;
  transition: string;
  reason_code: string;
  epoch: number;
  contract_ref: string | null;
  lease_ref: string | null;
  job_ref: string | null;
  created_at_ms: number;
  expires_at_ms: number;
}

@Component({
  selector: "app-semantic-debug-panel",
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      aria-labelledby="semantic-debug-title"
      data-testid="semantic-debug-read-only"
    >
      <h2 id="semantic-debug-title">Semantic-Media-Diagnose</h2>
      <p role="note">
        Nur lesbar · content-free · keine Steuerungs- oder Toolaktionen
      </p>
      @if (loading) {
        <p role="status" aria-live="polite">Audit wird geladen …</p>
      }
      @if (errorCode) {
        <p role="alert">Nicht verfügbar: {{ errorCode }}</p>
      }
      <ol aria-label="Autoritative Zustandswechsel">
        @for (event of events; track event.event_id) {
          <li>
            <strong>{{ event.event_type }} · {{ event.transition }}</strong>
            <span>Epoch {{ event.epoch }} · {{ event.reason_code }}</span>
            <code>{{ event.scope_digest }}</code>
            @if (event.contract_ref) {
              <span>Contract {{ event.contract_ref }}</span>
            }
            @if (event.lease_ref) {
              <span>Lease {{ event.lease_ref }}</span>
            }
            @if (event.job_ref) {
              <span>Job {{ event.job_ref }}</span>
            }
          </li>
        } @empty {
          <li>Keine freigegebenen Diagnoseereignisse.</li>
        }
      </ol>
      @if (nextCursor) {
        <button
          type="button"
          (click)="nextPage.emit(nextCursor)"
          [disabled]="loading"
        >
          Weitere Ereignisse laden
        </button>
      }
    </section>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      section {
        display: grid;
        gap: 0.75rem;
      }
      ol {
        display: grid;
        gap: 0.5rem;
        margin: 0;
        padding-inline-start: 1.5rem;
      }
      li {
        display: grid;
        gap: 0.2rem;
        overflow-wrap: anywhere;
      }
      code {
        font-size: 0.75rem;
      }
      button:focus-visible {
        outline: 3px solid currentColor;
        outline-offset: 2px;
      }
    `,
  ],
})
export class SemanticDebugPanelComponent {
  @Input({ required: true }) events: readonly SemanticDebugEventView[] = [];
  @Input() nextCursor: string | null = null;
  @Input() loading = false;
  @Input() errorCode: string | null = null;
  @Output() readonly nextPage = new EventEmitter<string>();
}
