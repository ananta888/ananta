import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-ops-log-viewer',
  standalone: true,
  imports: [FormsModule],
  template: `
    <section class="ops-log" aria-live="polite">
      <div class="ops-log-toolbar">
        <div>
          <strong>{{ title }}</strong>
          @if (truncated) { <span class="warning"> · Ausgabe gekürzt</span> }
        </div>
        <div class="row">
          @if (showTail) {
            <label class="compact-label">Zeilen
              <input type="number" min="1" max="1000" [(ngModel)]="tail" />
            </label>
          }
          <button type="button" class="secondary btn-small" (click)="reload.emit(normalizedTail())" [disabled]="loading">
            {{ loading ? 'Lädt…' : 'Neu laden' }}
          </button>
          <button type="button" class="secondary btn-small" (click)="copy()" [disabled]="!content">Kopieren</button>
          <button type="button" class="secondary btn-small" (click)="close.emit()">Schließen</button>
        </div>
      </div>
      @if (error) {
        <div class="state-banner error">{{ error }}</div>
      }
      <pre class="ops-log-content" tabindex="0">{{ content || (loading ? 'Lade Ausgabe…' : 'Keine Ausgabe vorhanden.') }}</pre>
      @if (stderr) {
        <details class="ops-stderr">
          <summary>stderr / Diagnoseausgabe</summary>
          <pre>{{ stderr }}</pre>
        </details>
      }
    </section>
  `,
  styles: [`
    .ops-log { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: #08111f; color: #dbeafe; }
    .ops-log-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; padding: 9px 12px; background: color-mix(in srgb, var(--card-bg) 90%, #08111f); color: var(--fg); }
    .compact-label { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; }
    .compact-label input { width: 72px; padding: 4px 6px; }
    .ops-log-content, .ops-stderr pre { margin: 0; padding: 12px; max-height: 460px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .ops-stderr { padding: 8px 12px; color: #fecaca; border-top: 1px solid #334155; }
    .ops-stderr pre { padding: 8px 0 0; max-height: 140px; }
  `],
})
export class OpsLogViewerComponent {
  @Input() title = 'Ausgabe';
  @Input() content = '';
  @Input() stderr = '';
  @Input() error = '';
  @Input() loading = false;
  @Input() truncated = false;
  @Input() tail = 200;
  @Input() showTail = true;
  @Output() reload = new EventEmitter<number>();
  @Output() close = new EventEmitter<void>();

  normalizedTail(): number {
    return Math.max(1, Math.min(1000, Number(this.tail) || 200));
  }

  async copy(): Promise<void> {
    if (this.content && globalThis.navigator?.clipboard) await globalThis.navigator.clipboard.writeText(this.content);
  }
}
