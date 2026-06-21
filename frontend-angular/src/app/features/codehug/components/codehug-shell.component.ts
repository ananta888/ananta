import { Component, ChangeDetectionStrategy, signal, HostListener } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/**
 * CodeHug Shell — Layout-Wurzel fuer die CodeHug-Spezialansicht.
 *
 * Layout: 3-Spalter auf Desktop, kollabierend auf Tablet/Handy.
 *
 * SOLID: SRP — Layout-Verwaltung und Viewport-Detection. Keine Business-Logik,
 * keine Daten-Aufrufe. Kinder rendern via <router-outlet>.
 */
@Component({
  selector: 'ch-shell',
  standalone: true,
  imports: [RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      class="codehug-shell"
      [attr.data-viewport]="viewport()"
      [attr.data-collapsed]="rightCollapsed() ? 'true' : 'false'">
      <header class="codehug-shell-header">
        <span class="codehug-brand">CodeHug</span>
        <span class="codehug-tagline">Code verstehen, Kontext bauen, Aenderungen sicher vorbereiten.</span>
        <div class="codehug-shell-spacer"></div>
        <span class="codehug-shell-write-mode" [attr.data-mode]="writeMode()">
          Modus: {{ writeMode() === 'read-only' ? 'Read-only' : 'Write armed' }}
        </span>
        <button
          type="button"
          class="codehug-shell-collapse"
          (click)="toggleRight()"
          [attr.aria-expanded]="!rightCollapsed()"
          aria-controls="codehug-right-panel"
          aria-label="Kontext-Bereich umschalten">
          {{ rightCollapsed() ? '>' : '<' }}
        </button>
      </header>

      <div class="codehug-shell-grid">
        <aside class="codehug-col codehug-col-left" aria-label="Projekt / Navigation">
          <ng-content select="[chLeft]" />
        </aside>

        <main class="codehug-col codehug-col-center" aria-label="Arbeitsbereich">
          <router-outlet />
        </main>

        <aside
          id="codehug-right-panel"
          class="codehug-col codehug-col-right"
          [class.codehug-col-right-collapsed]="rightCollapsed()"
          aria-label="Kontext / Agenten-Status">
          <ng-content select="[chRight]" />
        </aside>
      </div>
    </section>
  `,
  styles: [`
    :host { display: block; height: 100%; }
    .codehug-shell {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      background: var(--bg);
      color: var(--fg);
    }
    .codehug-shell-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 13px;
    }
    .codehug-brand { font-weight: 700; letter-spacing: 0.5px; }
    .codehug-tagline { color: var(--muted); font-size: 12px; }
    .codehug-shell-spacer { flex: 1; }
    .codehug-shell-write-mode[data-mode="read-only"] {
      padding: 3px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      font-size: 11px;
      font-weight: 600;
    }
    .codehug-shell-write-mode[data-mode="write-armed"] {
      padding: 3px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, #f59e0b 30%, transparent);
      color: #92400e;
      font-size: 11px;
      font-weight: 700;
    }
    .codehug-shell-collapse {
      padding: 4px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-weight: 700;
    }

    .codehug-shell-grid {
      flex: 1;
      display: grid;
      gap: 1px;
      min-height: 0;
      grid-template-columns: 240px 1fr 320px;
    }
    .codehug-col { min-width: 0; min-height: 0; overflow: auto; background: var(--bg); }
    .codehug-col-left, .codehug-col-right { border-right: 1px solid var(--border); padding: 12px; }
    .codehug-col-right { border-right: none; border-left: 1px solid var(--border); }
    .codehug-col-right-collapsed { display: none; }

    /* Tablet: 2 Spalten, rechte Spalte als Side-Panel */
    @media (max-width: 1023px) and (min-width: 768px) {
      .codehug-shell-grid { grid-template-columns: 200px 1fr; }
      .codehug-col-right {
        position: fixed;
        right: 0;
        top: 0;
        bottom: 0;
        width: 320px;
        z-index: 60;
        background: var(--bg);
        box-shadow: -10px 0 28px rgba(0,0,0,0.18);
      }
      .codehug-col-right-collapsed { display: none; }
    }

    /* Handy: Tabs */
    @media (max-width: 767px) {
      .codehug-shell-grid {
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr;
      }
      .codehug-col-left {
        border-right: none;
        border-bottom: 1px solid var(--border);
        max-height: 30vh;
      }
      .codehug-col-right {
        position: fixed;
        right: 0;
        top: 0;
        bottom: 0;
        width: 90vw;
        max-width: 360px;
        z-index: 60;
        background: var(--bg);
        box-shadow: -10px 0 28px rgba(0,0,0,0.18);
      }
    }
  `]
})
export class CodeHugShellComponent {
  readonly viewport = signal<'desktop' | 'tablet' | 'handy'>('desktop');
  readonly rightCollapsed = signal(false);
  /** Wird vom CodeHugFacade gesetzt; hier default read-only. */
  readonly writeMode = signal<'read-only' | 'write-armed'>('read-only');

  @HostListener('window:resize')
  onResize(): void {
    this.detectViewport();
  }

  ngOnInit(): void {
    this.detectViewport();
  }

  toggleRight(): void {
    this.rightCollapsed.update(v => !v);
  }

  private detectViewport(): void {
    const w = window.innerWidth;
    if (w >= 1024) this.viewport.set('desktop');
    else if (w >= 768) this.viewport.set('tablet');
    else this.viewport.set('handy');
  }
}