import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  standalone: true,
  selector: 'app-flow-tab',
  imports: [CommonModule],
  template: `
    <div class="flow-tab">
      <p class="hint">
        Zeigt den aktiven VisualProcess-Blueprint dieses Cases (read-only).
      </p>
      @if (caseId) {
        <div class="flow-stub">
          <p>Blueprint-Binding Ansicht — read-only</p>
          <p class="case-ref"><em>Case: {{ caseId }}</em></p>
          <p class="note">Vollständige VisualProcess-Darstellung via /api/caseflow/cases/{{caseId}} Blueprint-Binding.</p>
        </div>
      } @else {
        <p class="empty">Kein Case ausgewählt.</p>
      }
    </div>
  `,
  styles: [`
    .flow-tab { padding: 0.5rem 0; }
    .hint { color: #666; font-size: 0.85rem; margin-bottom: 1rem; }
    .flow-stub { background: #1e1e1e; padding: 1rem; border-radius: 6px; color: #aaa; }
    .case-ref { color: #60a5fa; font-family: monospace; }
    .note { font-size: 0.8rem; color: #555; }
    .empty { color: #555; }
  `],
})
export class FlowTabComponent {
  @Input() caseId = '';
}
