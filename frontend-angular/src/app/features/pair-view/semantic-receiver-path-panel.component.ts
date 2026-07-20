import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';

import {
  SemanticReceiverPathPreference,
  SemanticReceiverPathView,
} from '../../services/semantic-receiver-path.service';

export interface SemanticReceiverPathIntent {
  readonly receiverId: string;
  readonly preference: SemanticReceiverPathPreference;
}

@Component({
  selector: 'app-semantic-receiver-path-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="receiver-path-title">
      <h3 id="receiver-path-title">Receiver-spezifische Medienpfade</h3>
      <p class="notice">Die Auswahl ist ein Wunsch. Der effektive SFU-Pfad wird ausschließlich nach Hub-Admission aktiv.</p>
      @for (row of receivers; track row.receiverId) {
        <article [attr.data-receiver-id]="row.receiverId">
          <div>
            <strong>{{ row.label }}</strong>
            <span>Effektiv: {{ row.effectivePath === 'sfu' ? 'SFU' : 'Ordinary Media' }}</span>
            <span>{{ row.reasonCode }}</span>
          </div>
          <label>Gewünschter Pfad
            <select [value]="row.preference" (change)="select(row.receiverId, $any($event.target).value)">
              <option value="auto">Automatisch</option>
              <option value="ordinary">Ordinary</option>
              <option value="sfu">SFU</option>
            </select>
          </label>
          @if (row.pendingHubConfirmation) {
            <span role="status">Hub-Bestätigung ausstehend; effektiver Pfad bleibt unverändert.</span>
          }
        </article>
      } @empty {
        <p>Keine weiteren aktiven Receiver in der Pair-Session.</p>
      }
    </section>
  `,
  styles: [`
    :host { display: block; margin-top: 1rem; }
    section { border: 1px solid currentColor; border-radius: .6rem; padding: .8rem; }
    article { display: grid; grid-template-columns: minmax(12rem, 1fr) minmax(10rem, auto); gap: .6rem;
      border-top: 1px solid color-mix(in srgb, currentColor 25%, transparent); padding-block: .65rem; }
    article div, label { display: grid; gap: .25rem; }
    select { min-height: 2.5rem; }
    .notice { opacity: .8; }
    @media (max-width: 38rem) { article { grid-template-columns: 1fr; } }
  `],
})
export class SemanticReceiverPathPanelComponent {
  @Input() receivers: readonly SemanticReceiverPathView[] = [];
  @Output() readonly intent = new EventEmitter<SemanticReceiverPathIntent>();

  select(receiverId: string, value: string): void {
    if (!['auto', 'ordinary', 'sfu'].includes(value)) return;
    this.intent.emit({ receiverId, preference: value as SemanticReceiverPathPreference });
  }
}
