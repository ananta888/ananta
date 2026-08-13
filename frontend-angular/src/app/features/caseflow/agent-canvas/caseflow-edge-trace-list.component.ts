import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import type {
  CaseFlowEdgeTraceTelemetryEntry,
  CaseFlowTokenUsage,
} from './caseflow-edge-trace.models';

@Component({
  selector: 'app-caseflow-edge-trace-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (entries.length === 0) {
      <p class="trace-empty" role="status">Keine verifizierte Telemetrie verfügbar.</p>
    } @else {
      <ol class="trace-list" aria-label="Edge-Telemetrie">
        @for (entry of entries; track $index; let index = $index) {
          <li
            class="trace-entry"
            [class.highlighted]="highlightedIndex === index"
            [attr.data-telemetry-index]="index"
          >
            <header>
              <strong>{{ entry.event_type }}</strong>
              <span>{{ entry.status ?? unavailable }}</span>
            </header>
            <dl>
              <div><dt>Event-Referenz</dt><dd>{{ entry.event_ref ?? unavailable }}</dd></div>
              <div><dt>Trace-Referenz</dt><dd>{{ entry.trace_ref ?? unavailable }}</dd></div>
              <div><dt>Agent-Run-Referenz</dt><dd>{{ entry.agent_run_ref ?? unavailable }}</dd></div>
              <div><dt>Korrelation</dt><dd>{{ entry.correlation_ref ?? unavailable }}</dd></div>
              <div><dt>Ursache</dt><dd>{{ entry.causation_ref ?? unavailable }}</dd></div>
              <div><dt>Schritt</dt><dd>{{ entry.step_id ?? unavailable }}</dd></div>
              <div><dt>Sequenz</dt><dd>{{ display(entry.sequence) }}</dd></div>
              <div><dt>Zeitpunkt</dt><dd>{{ display(entry.occurred_at) }}</dd></div>
              <div><dt>Dauer (ms)</dt><dd>{{ display(entry.duration_ms) }}</dd></div>
              <div><dt>Modell</dt><dd>{{ entry.model ?? unavailable }}</dd></div>
              <div><dt>Provider</dt><dd>{{ entry.provider ?? unavailable }}</dd></div>
              <div><dt>Token-Nutzung</dt><dd>{{ displayTokenUsage(entry.token_usage) }}</dd></div>
              <div><dt>Kosten (µ)</dt><dd>{{ display(entry.cost_micros) }}</dd></div>
              <div><dt>Tool</dt><dd>{{ entry.tool ?? unavailable }}</dd></div>
              <div><dt>Fehler</dt><dd>{{ entry.error ?? unavailable }}</dd></div>
            </dl>
          </li>
        }
      </ol>
    }
  `,
  styleUrl: './caseflow-edge-trace-list.component.scss',
})
export class CaseFlowEdgeTraceListComponent {
  @Input() entries: readonly CaseFlowEdgeTraceTelemetryEntry[] = [];
  @Input() highlightedIndex: number | null = null;

  readonly unavailable = 'Nicht verfügbar';

  display(value: number | null): string {
    return value === null ? this.unavailable : String(value);
  }

  displayTokenUsage(value: CaseFlowTokenUsage | null): string {
    if (value === null) return this.unavailable;
    const entries = Object.entries(value);
    return entries.length === 0
      ? this.unavailable
      : entries.map(([key, amount]) => `${key}: ${amount}`).join(', ');
  }
}
