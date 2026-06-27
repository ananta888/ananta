
import { Component } from '@angular/core';

import { DEMO_GAME_MAP, GameMapUiContract, GameMapTerritoryView } from '../features/strategy-game/game-map.contract';

@Component({
  standalone: true,
  selector: 'app-strategy-game-demo',
  imports: [],
  template: `
    <section class="card strategy-demo">
      <h2>{{ map.title }}</h2>
      <p class="muted">2D Demo-Ansicht auf Basis des GameMap-JSON-Vertrags, ohne Live-Agenten-Ausfuehrung.</p>

      <div class="grid cols-3 mt-md">
        @for (territory of map.territories; track territory.id) {
          <article class="card card-light territory" [class.blocked]="territory.visibility === 'blocked' || territory.visibility === 'hidden' || territory.visibility === 'redacted'">
            <header>
              <strong>{{ territory.name }}</strong>
              <span class="badge" [class.risk]="isRisky(territory)">{{ territory.riskLevel }}</span>
            </header>
            <div class="muted">{{ territory.path }}</div>
            <div class="state">visibility: {{ territory.visibility }}</div>
          </article>
        }
      </div>

      <div class="mt-lg">
        <h3>Legende</h3>
        <p class="no-margin muted">
          <strong>Sichtbar</strong> = sichtbares Territorium, <strong>Blocked/Hidden/Redacted</strong> = gesperrt oder maskiert,
          <strong>risk=high/critical</strong> = gefaehrdet.
        </p>
      </div>
    </section>
  `,
  styles: [`
    .strategy-demo { max-width: 1100px; margin: 0 auto; }
    .territory { min-height: 120px; border-left: 4px solid #4f46e5; }
    .territory.blocked { border-left-color: #dc2626; opacity: 0.9; }
    .territory header { display: flex; justify-content: space-between; gap: 8px; }
    .badge { font-size: 12px; padding: 2px 6px; border-radius: 999px; background: #e2e8f0; }
    .badge.risk { background: #fee2e2; color: #991b1b; }
    .state { margin-top: 6px; font-size: 13px; }
  `],
})
export class StrategyGameDemoComponent {
  map: GameMapUiContract = DEMO_GAME_MAP;

  isRisky(territory: GameMapTerritoryView): boolean {
    return territory.riskLevel === 'high' || territory.riskLevel === 'critical';
  }
}
