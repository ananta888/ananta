import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * CodeHug Dashboard — Einstiegsansicht fuer /codehug.
 *
 * Zeigt Projekt-Uebersicht und CodeCompass-Status. Konkrete Daten-Logik
 * kommt in CH-002 (eigene Tasks), diese Komponente ist der Layout-Anker.
 */
@Component({
  selector: 'ch-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-dashboard">
      <h2 class="ch-dashboard-title">CodeHug Dashboard</h2>
      <p class="ch-dashboard-lead">
        Zentraler Einstieg fuer Code-Verstehen, Kontext-Aufbau und sichere Agenten-Interaktion.
      </p>

      <div class="ch-dashboard-grid">
        <article class="ch-card" aria-labelledby="ch-projects-h">
          <h3 id="ch-projects-h">Projekt</h3>
          <p class="ch-card-empty">Noch kein Projekt ausgewaehlt.</p>
        </article>

        <article class="ch-card" aria-labelledby="ch-cc-h">
          <h3 id="ch-cc-h">CodeCompass-Status</h3>
          <p class="ch-card-empty">Status wird geladen…</p>
        </article>

        <article class="ch-card" aria-labelledby="ch-runs-h">
          <h3 id="ch-runs-h">Letzte Agent-Runs</h3>
          <p class="ch-card-empty">Keine laufenden Runs.</p>
        </article>
      </div>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 18px; }
    .ch-dashboard-title { margin: 0 0 4px; font-size: 20px; }
    .ch-dashboard-lead { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
    .ch-dashboard-grid {
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    }
    .ch-card {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      background: var(--card-bg);
    }
    .ch-card h3 { margin: 0 0 8px; font-size: 14px; }
    .ch-card-empty { color: var(--muted); font-size: 12px; margin: 0; }
  `]
})
export class CodeHugDashboardComponent {}