import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-public-pair-page',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="public-pair-page" data-testid="public-pair-page" aria-labelledby="public-pair-title">
      <header class="public-pair-header">
        <div>
          <h1 id="public-pair-title">Public Pair Dev</h1>
          <p>Direkte, Ende-zu-Ende-verschlüsselte Pair-Session</p>
        </div>
      </header>

      <section class="public-pair-drawer-handoff" data-testid="public-pair-drawer-handoff">
        <h2>Pair Dev läuft im AI-Snake-Fenster</h2>
        <p>
          Session und Pair-Chat sind rechts unten im Tab „Pair Dev“ geöffnet.
          Kamera-, Bildschirm- und Audiosteuerung liegt getrennt im Tab „Medien“.
          Minimieren oder Ausblenden lässt eine aktive Session und Medienfreigaben weiterlaufen.
        </p>
      </section>
    </section>
  `,
  styles: [`
    :host { display: block; min-height: 100%; }
    .public-pair-page {
      box-sizing: border-box; max-width: 1080px; margin: 0 auto; padding: 20px;
      color: var(--fg); font-family: ui-monospace, Menlo, Consolas, monospace;
    }
    .public-pair-header, .public-pair-drawer-handoff {
      padding: 16px; border: 1px solid var(--border);
      border-radius: 8px; background: var(--card-bg);
    }
    .public-pair-drawer-handoff { margin-top: 14px; }
    h1, h2 { margin: 0 0 6px; }
    h1 { font-size: 20px; }
    h2 { font-size: 16px; }
    p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    @media (max-width: 640px) { .public-pair-page { padding: 10px; } }
  `],
})
export class PublicPairPageComponent {}
