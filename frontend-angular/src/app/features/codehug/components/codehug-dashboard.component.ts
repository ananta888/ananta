import { Component, ChangeDetectionStrategy, OnInit, inject } from '@angular/core';
import { DatePipe } from '@angular/common';
import { CodeHugFacade } from '../state/codehug.facade';
import { ChIndexStatus, ChAgentRunReadModel } from '../models/codehug.models';

/**
 * CodeHug Dashboard — Einstiegsansicht fuer /codehug.
 *
 * CH-002-001 (Projektuebersicht) + CH-002-002 (CodeCompass-Status).
 * Zeigt Projekt-Auswahl, Metadaten, Index-Status, Sensitive-Pattern-Hinweis,
 * Re-Index-Button und letzte Agent-Runs.
 */
@Component({
  selector: 'ch-dashboard',
  standalone: true,
  imports: [DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-dashboard">
      <header class="ch-dashboard-head">
        <h2 class="ch-dashboard-title">CodeHug Dashboard</h2>
        <p class="ch-dashboard-lead">
          Zentraler Einstieg fuer Code-Verstehen, Kontext-Aufbau und sichere Agenten-Interaktion.
        </p>
      </header>

      <!-- Projektauswahl -->
      <section class="ch-card" aria-labelledby="ch-projects-h">
        <header class="ch-card-head">
          <h3 id="ch-projects-h">Projekt</h3>
          @if (facade.projects().length > 0) {
            <select
              class="ch-select"
              [value]="facade.currentProjectId() ?? ''"
              (change)="onProjectChange($any($event.target).value)"
              aria-label="Projekt auswaehlen">
              <option value="" disabled>Projekt waehlen…</option>
              @for (p of facade.projects(); track p.id) {
                <option [value]="p.id">{{ p.name }}</option>
              }
            </select>
          }
        </header>

        @if (facade.projectError(); as err) {
          <p class="ch-error" role="alert">Fehler: {{ err }}</p>
        }

        @if (facade.loadingProject()) {
          <p class="ch-muted">Projekt wird geladen…</p>
        }

        @if (facade.currentProject(); as proj) {
          <dl class="ch-meta">
            <dt>Name</dt><dd>{{ proj.name }}</dd>
            <dt>Pfad</dt><dd class="ch-mono">{{ proj.rootPath }}</dd>
            <dt>Dateien</dt><dd>{{ proj.fileCount }}</dd>
            <dt>Symbole</dt><dd>{{ proj.symbolCount }}</dd>
            <dt>Module</dt><dd>{{ proj.moduleCount }}</dd>
            @if (proj.lastIndexedAt) {
              <dt>Zuletzt indexiert</dt>
              <dd>{{ proj.lastIndexedAt | date: 'medium' }}</dd>
            }
          </dl>

          @if (proj.frameworkSignals.length > 0) {
            <p class="ch-tags-label">Frameworks / Signale</p>
            <ul class="ch-tags">
              @for (f of proj.frameworkSignals; track f) {
                <li class="ch-tag">{{ f }}</li>
              }
            </ul>
          }
        } @else if (!facade.loadingProject() && facade.projects().length === 0) {
          <p class="ch-muted">Keine Projekte verfuegbar. Backend antwortet nicht oder keine Projekte indexiert.</p>
        }
      </section>

      <!-- CodeCompass-Status -->
      <section class="ch-card" aria-labelledby="ch-cc-h">
        <header class="ch-card-head">
          <h3 id="ch-cc-h">CodeCompass-Status</h3>
          @if (facade.currentProject(); as proj) {
            <button
              type="button"
              class="ch-btn ch-btn-secondary"
              (click)="facade.triggerReindex()"
              [disabled]="proj.indexStatus === 'running'">
              {{ proj.indexStatus === 'running' ? 'Re-Indexierung laeuft…' : 'Re-Indexieren' }}
            </button>
          }
        </header>

        @if (facade.currentProject(); as proj) {
          <p class="ch-status" [attr.data-status]="proj.indexStatus">
            <span class="ch-status-dot" aria-hidden="true"></span>
            <strong>{{ statusLabel(proj.indexStatus) }}</strong>
            @if (proj.indexStatus === 'partial') {
              <span class="ch-muted"> — einige Dateien sind nicht erfasst.</span>
            } @else if (proj.indexStatus === 'missing') {
              <span class="ch-muted"> — keine Indexdaten vorhanden.</span>
            } @else if (proj.indexStatus === 'error') {
              <span class="ch-muted"> — Indexierung fehlgeschlagen.</span>
            }
          </p>

          @if (proj.languageBreakdown && keyCount(proj.languageBreakdown) > 0) {
            <p class="ch-tags-label">Sprachen</p>
            <ul class="ch-tags">
              @for (entry of languageEntries(proj.languageBreakdown); track entry.key) {
                <li class="ch-tag">{{ entry.key }} <span class="ch-tag-count">{{ entry.value }}</span></li>
              }
            </ul>
          }
        } @else {
          <p class="ch-muted">Status wird sichtbar, sobald ein Projekt ausgewaehlt ist.</p>
        }
      </section>

      <!-- Letzte Agent-Runs -->
      <section class="ch-card" aria-labelledby="ch-runs-h">
        <h3 id="ch-runs-h">Letzte Agent-Runs</h3>
        @if (facade.recentRuns().length === 0) {
          <p class="ch-muted">Keine laufenden oder kuerzlich abgeschlossenen Runs.</p>
        } @else {
          <ul class="ch-runs">
            @for (run of facade.recentRuns(); track run.id) {
              <li class="ch-run" [attr.data-status]="run.status">
                <span class="ch-run-status">{{ statusBadge(run) }}</span>
                <span class="ch-run-id ch-mono">{{ run.id }}</span>
                <span class="ch-run-backend">{{ run.actualCliBackend }}</span>
                <span class="ch-run-when">{{ run.startedAt | date: 'short' }}</span>
              </li>
            }
          </ul>
        }
      </section>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 18px; }
    .ch-dashboard-head { margin-bottom: 14px; }
    .ch-dashboard-title { margin: 0 0 4px; font-size: 20px; }
    .ch-dashboard-lead { margin: 0; color: var(--muted); font-size: 13px; }
    .ch-card {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      background: var(--card-bg);
      margin-bottom: 14px;
    }
    .ch-card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .ch-card h3 { margin: 0; font-size: 14px; }
    .ch-muted { color: var(--muted); font-size: 12px; margin: 4px 0 0; }
    .ch-error { color: #b91c1c; font-size: 12px; margin: 4px 0 0; }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); font-size: 12px; }

    .ch-meta {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 4px 12px;
      margin: 8px 0 0;
      font-size: 12px;
    }
    .ch-meta dt { color: var(--muted); }
    .ch-meta dd { margin: 0; }

    .ch-tags-label {
      margin: 10px 0 4px;
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .ch-tags {
      list-style: none;
      padding: 0;
      margin: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .ch-tag {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 16%, transparent);
    }
    .ch-tag-count {
      margin-left: 4px;
      opacity: 0.7;
    }

    .ch-status {
      margin: 6px 0 0;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
    }
    .ch-status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--muted);
      display: inline-block;
    }
    .ch-status[data-status="complete"] .ch-status-dot { background: #10b981; }
    .ch-status[data-status="partial"] .ch-status-dot { background: #f59e0b; }
    .ch-status[data-status="missing"] .ch-status-dot,
    .ch-status[data-status="error"] .ch-status-dot { background: #ef4444; }
    .ch-status[data-status="running"] .ch-status-dot {
      background: #3b82f6;
      animation: ch-pulse 1.4s ease-in-out infinite;
    }
    @keyframes ch-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    .ch-select {
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      font-size: 12px;
    }
    .ch-btn {
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-btn-secondary { background: var(--card-bg); }
    .ch-btn:disabled { opacity: 0.5; cursor: not-allowed; }

    .ch-runs {
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 4px;
    }
    .ch-run {
      display: grid;
      grid-template-columns: 80px 1fr max-content max-content;
      gap: 8px;
      align-items: center;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
    }
    .ch-run-status {
      font-weight: 600;
      padding: 2px 6px;
      border-radius: 4px;
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      text-align: center;
      font-size: 11px;
    }
    .ch-run[data-status="failed"] .ch-run-status {
      background: color-mix(in srgb, #ef4444 20%, transparent);
      color: #991b1b;
    }
    .ch-run[data-status="running"] .ch-run-status {
      background: color-mix(in srgb, #3b82f6 20%, transparent);
      color: #1e40af;
    }
    .ch-run[data-status="succeeded"] .ch-run-status {
      background: color-mix(in srgb, #10b981 20%, transparent);
      color: #065f46;
    }
    .ch-run-backend {
      font-size: 11px;
      color: var(--muted);
      padding: 2px 6px;
      border-radius: 4px;
      background: var(--bg);
    }
  `]
})
export class CodeHugDashboardComponent implements OnInit {
  readonly facade = inject(CodeHugFacade);

  ngOnInit(): void {
    this.facade.loadProjects();
  }

  onProjectChange(projectId: string): void {
    if (!projectId) return;
    this.facade.selectProject(projectId);
  }

  statusLabel(status: ChIndexStatus): string {
    const labels: Record<ChIndexStatus, string> = {
      complete: 'Vollstaendig indexiert',
      partial: 'Teilweise indexiert',
      missing: 'Keine Indexdaten',
      running: 'Indexierung laeuft',
      error: 'Fehler',
    };
    return labels[status] ?? status;
  }

  statusBadge(run: ChAgentRunReadModel): string {
    const s = run.status;
    const map: Record<string, string> = {
      pending: 'wartet',
      running: 'laeuft',
      awaiting_approval: 'Approval',
      awaiting_diff_review: 'Diff-Pruefung',
      awaiting_apply_confirmation: 'Apply-Bestaetigung',
      succeeded: 'OK',
      failed: 'FAIL',
      cancelled: 'abgebrochen',
      rolled_back: 'rollback',
    };
    return map[s] ?? s;
  }

  languageEntries(breakdown: Record<string, number>): { key: string; value: number }[] {
    return Object.entries(breakdown)
      .sort((a, b) => b[1] - a[1])
      .map(([key, value]) => ({ key, value }));
  }

  keyCount(obj: Record<string, unknown>): number {
    return Object.keys(obj).length;
  }
}