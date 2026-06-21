import { Component, ChangeDetectionStrategy, signal, computed, HostListener, inject, OnDestroy } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive, ActivatedRoute } from '@angular/router';
import { PolicyService } from '../services/policy.service';

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
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ActivatedRoute],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      class="codehug-shell"
      [attr.data-viewport]="viewport()"
      [attr.data-collapsed]="rightCollapsed() ? 'true' : 'false'">
      <header class="codehug-shell-header">
        <a routerLink="/codehug" class="codehug-brand" aria-label="CodeHug Dashboard">
          <span class="codehug-brand-icon" aria-hidden="true">⬡</span>
          <span class="codehug-brand-text">CodeHug</span>
        </a>

        <nav class="codehug-shell-nav" aria-label="CodeHug-Bereiche">
          <a routerLink="/codehug" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Dashboard</a>
          <a routerLink="/codehug/context" routerLinkActive="active">Kontext</a>
          <a routerLink="/codehug/search" routerLinkActive="active">Suche</a>
          <a routerLink="/codehug/refactoring" routerLinkActive="active">Refactoring</a>
          <a routerLink="/codehug/agents" routerLinkActive="active">Agenten</a>
          <a routerLink="/codehug/internals" routerLinkActive="active">Internals</a>
          <a routerLink="/codehug/policy" routerLinkActive="active">Policy</a>
        </nav>

        <div class="codehug-shell-spacer"></div>

        <!-- Write-Mode Badge + Toggle -->
        <div class="codehug-wm-cluster">
          @if (writeModeExpiresIn() !== null) {
            <span class="codehug-wm-timer">{{ writeModeExpiresIn() }}s</span>
          }
          <span
            class="codehug-shell-write-mode"
            data-testid="write-mode-badge"
            [attr.data-mode]="policy.writeMode()">
            @if (policy.writeMode() === 'read-only') {
              <span class="codehug-wm-dot"></span> read-only
            } @else {
              <span class="codehug-wm-dot codehug-wm-dot-armed"></span> write-armed
            }
          </span>
          @if (policy.writeMode() === 'read-only') {
            <button
              type="button"
              class="codehug-wm-btn codehug-wm-btn-arm"
              (click)="armWriteMode()"
              title="Write-Modus aktivieren (15 min)">
              Arm
            </button>
          } @else {
            <button
              type="button"
              class="codehug-wm-btn codehug-wm-btn-disarm"
              (click)="policy.disarmWriteMode()"
              title="Write-Modus beenden">
              Disarm
            </button>
          }
        </div>

        <button
          type="button"
          class="codehug-shell-collapse"
          (click)="toggleRight()"
          [attr.aria-expanded]="!rightCollapsed()"
          aria-controls="codehug-right-panel"
          aria-label="Kontext-Bereich umschalten">
          {{ rightCollapsed() ? '▶' : '◀' }}
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
      gap: 10px;
      padding: 8px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 13px;
      min-height: 44px;
    }

    /* Brand */
    .codehug-brand {
      display: flex;
      align-items: center;
      gap: 6px;
      text-decoration: none;
      color: inherit;
    }
    .codehug-brand-icon {
      font-size: 18px;
      color: var(--accent);
      line-height: 1;
    }
    .codehug-brand-text {
      font-weight: 700;
      font-size: 14px;
      letter-spacing: 0.3px;
    }

    /* Nav */
    .codehug-shell-nav {
      display: flex;
      align-items: center;
      gap: 2px;
    }
    .codehug-shell-nav a {
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      text-decoration: none;
      color: var(--muted);
      transition: background 0.12s, color 0.12s;
      white-space: nowrap;
    }
    .codehug-shell-nav a:hover { background: var(--bg); color: var(--fg); }
    .codehug-shell-nav a.active {
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      color: var(--accent);
      font-weight: 600;
    }

    .codehug-shell-spacer { flex: 1; }

    /* Write-mode cluster */
    .codehug-wm-cluster {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .codehug-wm-timer {
      font-size: 11px;
      color: #f59e0b;
      font-weight: 700;
      min-width: 32px;
      text-align: right;
    }
    .codehug-shell-write-mode {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 3px 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      background: color-mix(in srgb, var(--accent) 10%, transparent);
      border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    }
    .codehug-shell-write-mode[data-mode="write-armed"] {
      background: color-mix(in srgb, #f59e0b 22%, transparent);
      border-color: #f59e0b;
      color: #92400e;
    }
    .codehug-wm-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--muted);
      flex-shrink: 0;
    }
    .codehug-wm-dot-armed {
      background: #f59e0b;
      box-shadow: 0 0 5px #f59e0b;
      animation: ch-wm-pulse 1.6s ease-in-out infinite;
    }
    @keyframes ch-wm-pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
    .codehug-wm-btn {
      padding: 3px 9px;
      border-radius: 6px;
      border: 1px solid var(--border);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
    }
    .codehug-wm-btn-arm {
      background: color-mix(in srgb, #f59e0b 14%, transparent);
      color: #78350f;
      border-color: #f59e0b;
    }
    .codehug-wm-btn-arm:hover { background: color-mix(in srgb, #f59e0b 25%, transparent); }
    .codehug-wm-btn-disarm {
      background: var(--card-bg);
      color: var(--muted);
    }

    .codehug-shell-collapse {
      padding: 4px 9px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .codehug-shell-collapse:hover { background: var(--card-bg); }

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
export class CodeHugShellComponent implements OnDestroy {
  readonly viewport = signal<'desktop' | 'tablet' | 'handy'>('desktop');
  readonly rightCollapsed = signal(false);
  readonly policy = inject(PolicyService);

  readonly writeModeExpiresIn = computed(() => {
    const exp = this.policy.writeModeExpiresAt();
    if (!exp || this.policy.writeMode() === 'read-only') return null;
    return Math.max(0, Math.round((exp - Date.now()) / 1000));
  });

  private _timerHandle: ReturnType<typeof setInterval> | null = null;
  private _tick = signal(0);

  constructor() {
    this.detectViewport();
    this._timerHandle = setInterval(() => {
      if (this.policy.writeMode() === 'write-armed') {
        this.policy.ensureWriteModeValid();
        this._tick.update(n => n + 1);
      }
    }, 1000);
  }

  ngOnDestroy(): void {
    if (this._timerHandle) clearInterval(this._timerHandle);
  }

  @HostListener('window:resize')
  onResize(): void {
    this.detectViewport();
  }

  toggleRight(): void {
    this.rightCollapsed.update(v => !v);
  }

  armWriteMode(): void {
    const ok = confirm('Write-Modus für 15 Minuten aktivieren?\nSchreibende Aktionen sind dann möglich.');
    if (ok) this.policy.armWriteMode();
  }

  private detectViewport(): void {
    const w = window.innerWidth;
    if (w >= 1024) this.viewport.set('desktop');
    else if (w >= 768) this.viewport.set('tablet');
    else this.viewport.set('handy');
  }
}