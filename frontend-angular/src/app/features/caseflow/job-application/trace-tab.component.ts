import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  standalone: true,
  selector: 'app-trace-tab',
  imports: [CommonModule],
  template: `
    <div class="trace-tab">
      <p class="hint">
        Trace-Ansicht zeigt KI-Aktivitäten, die für diesen Case aufgezeichnet wurden.
      </p>
      @if (caseId) {
        <p class="stub">
          Trace-Viewer stub — verlinkt auf bestehenden AI-Snake Trace Viewer.
          <br />
          <em>Case-ID: {{ caseId }}</em>
        </p>
        <!-- In der vollständigen Implementierung: ai-snake-trace-viewer mit case_id-Filter -->
      } @else {
        <p class="empty">Kein Case ausgewählt.</p>
      }
    </div>
  `,
  styles: [`
    .trace-tab { padding: 0.5rem 0; }
    .hint { color: #666; font-size: 0.85rem; margin-bottom: 1rem; }
    .stub { background: #1e1e1e; padding: 1rem; border-radius: 6px; color: #aaa; }
    .stub em { color: #60a5fa; font-family: monospace; }
    .empty { color: #555; }
  `],
})
export class TraceTabComponent {
  @Input() caseId = '';
}
