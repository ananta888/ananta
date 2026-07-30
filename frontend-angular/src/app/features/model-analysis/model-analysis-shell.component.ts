import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ModelAnalysisFacade, isCancellableStatus } from './model-analysis.facade';
import { ModelAnalysisGraphComponent } from './model-analysis-graph.component';
import { ModelAnalysisJobStatus } from './model-analysis.models';

@Component({
  selector: 'app-model-analysis-shell',
  standalone: true,
  imports: [FormsModule, ModelAnalysisGraphComponent],
  providers: [ModelAnalysisFacade],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="analysis-shell" aria-labelledby="analysis-title">
      <header class="hero">
        <div>
          <p class="kicker">Model intelligence · Hub controlled</p>
          <h1 id="analysis-title">Open-Weight Modellanalyse</h1>
          <p class="lede">Importreferenz prüfen, Analyse delegieren und reproduzierbare Reports untersuchen.</p>
        </div>
        <button type="button" class="refresh" (click)="facade.loadOverview()" [disabled]="facade.loadingOverview()">
          Aktualisieren
        </button>
      </header>
      <p class="interpretation-link">
        Ergebnisse sind technische Evidenz, keine automatische Kausalitätsaussage.
        <a href="/docs/model-intelligence/interpretation.md" target="_blank" rel="noopener">
          Interpretationsleitfaden öffnen
        </a>
      </p>

      <div class="state-announcer" aria-live="polite" aria-atomic="true">
        @switch (facade.viewState()) {
          @case ('loading') {
            <section class="state-card" role="status" data-testid="model-analysis-loading">
              <span class="loader" aria-hidden="true"></span>
              <div><h2>Analysen werden geladen</h2><p>Capabilities und Jobstatus werden beim Hub abgefragt.</p><p>Reason-Code: <code>{{ facade.stateReasonCode() }}</code></p></div>
            </section>
          }
          @case ('permission') {
            <section class="state-card state-denied" role="alert" data-testid="model-analysis-permission">
              <div><h2>Keine Berechtigung</h2><p>Die Modellanalyse benötigt eine passende Hub-Rolle. Anmeldung und Rollenzuweisung prüfen.</p><p>Reason-Code: <code>{{ facade.stateReasonCode() }}</code></p></div>
            </section>
          }
          @case ('unsupported') {
            <section class="state-card state-unsupported" role="status" data-testid="model-analysis-unsupported">
              <div><h2>Modellanalyse nicht unterstützt</h2><p>Der Hub hat diesen Analysepfad ausdrücklich als nicht unterstützt abgelehnt.</p><p>Reason-Code: <code>{{ facade.stateReasonCode() }}</code></p></div>
            </section>
          }
          @case ('error') {
            <section class="state-card state-error" role="alert" data-testid="model-analysis-error">
              <div><h2>Analyseoberfläche nicht verfügbar</h2><p>{{ facade.error() }}</p><p>Reason-Code: <code>{{ facade.stateReasonCode() }}</code></p></div>
              <button type="button" (click)="facade.loadOverview()">Erneut versuchen</button>
            </section>
          }
        }
      </div>

      @if (facade.viewState() === 'ready' || facade.viewState() === 'empty') {
        <section class="start-panel" aria-labelledby="start-title">
          <div>
            <p class="section-number">01 / Delegation</p>
            <h2 id="start-title">Analyse starten</h2>
            <p>Nur eine bereits zugelassene Importreferenz wird an den Hub übergeben.</p>
          </div>
          <form (ngSubmit)="start()" class="start-form">
            <label for="analysis-import-ref">Importreferenz</label>
            <div class="input-row">
              <input
                id="analysis-import-ref"
                name="importRef"
                [(ngModel)]="importRef"
                required
                maxlength="512"
                autocomplete="off"
                aria-describedby="import-ref-help"
                placeholder="import:model_…" />
              <button type="submit" [disabled]="facade.mutating() || !importRef.trim()">
                {{ facade.mutating() ? 'Wird gestartet …' : 'Analyse delegieren' }}
              </button>
            </div>
            <p id="import-ref-help" class="field-help">Keine lokalen Dateipfade oder Uploads; der Hub löst die Referenz tenantgebunden auf.</p>
          </form>
        </section>

        <div class="workspace-grid">
          <section class="jobs-panel" aria-labelledby="jobs-title">
            <div class="panel-heading">
              <div><p class="section-number">02 / Queue</p><h2 id="jobs-title">Analysejobs</h2></div>
              <span class="count">{{ facade.jobs().length }}</span>
            </div>
            @if (facade.viewState() === 'empty') {
              <div class="empty-state" role="status" data-testid="model-analysis-empty">
                <strong>Noch keine Analysejobs</strong>
                <p>Eine Importreferenz startet den deterministischen Weg bis zum Report.</p>
                <p>Reason-Code: <code>{{ facade.stateReasonCode() }}</code></p>
              </div>
            } @else {
              <ul class="job-list" aria-label="Analysejobs">
                @for (job of facade.jobs(); track job.job_id) {
                  <li>
                    <button
                      type="button"
                      class="job-row"
                      [class.selected]="facade.selectedJob()?.job_id === job.job_id"
                      [attr.aria-current]="facade.selectedJob()?.job_id === job.job_id ? 'true' : null"
                      (click)="facade.selectJob(job.job_id)">
                      <span>
                        <strong>{{ job.model_id }}</strong>
                        <code>{{ job.job_id }}</code>
                      </span>
                      <span class="status" [attr.data-status]="job.status">{{ statusLabel(job.status) }}</span>
                    </button>
                  </li>
                }
              </ul>
              @if (facade.nextCursor()) {
                <button type="button" class="load-more" (click)="facade.loadMore()" [disabled]="facade.loadingOverview()">
                  Weitere Jobs laden
                </button>
              }
            }
          </section>

          <section class="detail-panel" aria-labelledby="detail-title">
            <div class="panel-heading">
              <div><p class="section-number">03 / Evidence</p><h2 id="detail-title">Status & Report</h2></div>
              @if (facade.selectedJob()) {
                <button type="button" class="icon-action" (click)="facade.refreshSelected()" aria-label="Ausgewählten Job aktualisieren">↻</button>
              }
            </div>
            @if (facade.loadingSelection()) {
              <p role="status">Jobdetails werden geladen …</p>
            } @else if (!facade.selectedJob()) {
              <p class="empty-state">Einen Job auswählen, um Status, Report und Graph zu sehen.</p>
            } @else {
              @let job = facade.selectedJob()!;
              <article class="job-detail">
                <div class="detail-status">
                  <span class="status" [attr.data-status]="job.status">{{ statusLabel(job.status) }}</span>
                  <strong>{{ job.progress_percent }} %</strong>
                </div>
                <progress [value]="job.progress_percent" max="100" [attr.aria-label]="'Fortschritt ' + job.progress_percent + ' Prozent'"></progress>
                <dl>
                  <div><dt>Job</dt><dd><code>{{ job.job_id }}</code></dd></div>
                  <div><dt>Modell</dt><dd>{{ job.model_id }}</dd></div>
                  <div><dt>Profil</dt><dd>{{ job.profile_id }}</dd></div>
                  @if (job.reason_code) { <div><dt>Reason-Code</dt><dd><code>{{ job.reason_code }}</code></dd></div> }
                </dl>
                @if (canCancel(job.status)) {
                  <button type="button" class="cancel" (click)="facade.cancelSelected()" [disabled]="facade.mutating()">Job abbrechen</button>
                }
              </article>

              @if (facade.report()) {
                <section class="report" aria-labelledby="report-title">
                  <div class="report-heading">
                    <h3 id="report-title">Kanonischer Report</h3>
                    <code>{{ facade.report()!.content_digest }}</code>
                  </div>
                  @for (section of facade.report()!.sections; track section.name) {
                    <article class="report-section" [attr.data-section-status]="section.status">
                      <header><h4>{{ section.name }}</h4><span>{{ section.status }}</span></header>
                      @if (section.reason_code) { <p><strong>Reason:</strong> <code>{{ section.reason_code }}</code></p> }
                      @if (section.status === 'available') {
                        <pre>{{ formatData(section.data) }}</pre>
                      } @else {
                        <p>{{ sectionStateText(section.status) }}</p>
                        <a href="/docs/model-intelligence/interpretation.md" target="_blank" rel="noopener">
                          Status richtig interpretieren
                        </a>
                      }
                    </article>
                  } @empty {
                    <p role="status">Der Report enthält keine Abschnitte.</p>
                  }
                </section>
              }
            }
          </section>
        </div>

        @if (facade.graph()) {
          <app-model-analysis-graph [graph]="facade.graph()" />
        }
      }
    </main>
  `,
  styles: [`
    :host{display:block;background:linear-gradient(135deg,#ece5d5 0,#f8f5ec 45%,#dce7e3 100%);min-height:100%}
    .analysis-shell{--ink:#18372d;--paper:#fffdf6;--red:#a33b20;--teal:#1c6e78;display:grid;gap:1.25rem;max-width:92rem;margin:auto;padding:clamp(1rem,3vw,2.5rem);color:var(--ink)}
    .hero{display:flex;justify-content:space-between;gap:2rem;align-items:end;border-bottom:4px solid var(--ink);padding:1.25rem 0}.kicker,.section-number{font:700 .75rem "Courier New",monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--red)}
    h1,h2,h3,h4{font-family:Georgia,serif}h1{font-size:clamp(2.2rem,5vw,4.8rem);line-height:.95;max-width:12ch;margin:.4rem 0}.lede{font-size:1.05rem;max-width:52ch}
    button,input{font:inherit}button{background:var(--ink);border:2px solid var(--ink);color:#fff;cursor:pointer;font-weight:700;padding:.65rem .9rem}button:hover:not(:disabled){background:var(--red);border-color:var(--red)}button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #e5a927;outline-offset:3px}button:disabled{cursor:not-allowed;opacity:.55}
    .refresh,.load-more,.icon-action{background:transparent;color:var(--ink)}.state-card,.start-panel,.jobs-panel,.detail-panel{background:var(--paper);border:1px solid #9ca89f;box-shadow:5px 5px 0 var(--ink);padding:1.25rem}
    .interpretation-link{background:#18372d;color:#fff;margin:0;padding:.75rem 1rem}.interpretation-link a{color:#ffd47a;font-weight:700}
    .state-card{display:flex;align-items:center;gap:1rem}.state-card h2{margin:.1rem 0}.state-denied{border-left:8px solid #a33b20}.state-unsupported{border-left:8px solid #b97713}.state-error{border-left:8px solid #8f1d14;justify-content:space-between}
    .loader{border:4px solid #bdc8c3;border-top-color:var(--red);border-radius:50%;height:2rem;width:2rem;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
    .start-panel{display:grid;grid-template-columns:minmax(14rem,.7fr) minmax(20rem,1.3fr);gap:2rem;align-items:end}.start-panel h2,.panel-heading h2{margin:.15rem 0}.start-form{display:grid;gap:.45rem}.start-form label{font-weight:700}.input-row{display:grid;grid-template-columns:1fr auto;gap:.5rem}.input-row input{border:2px solid var(--ink);min-width:0;padding:.75rem}.field-help{font-size:.82rem;margin:0;color:#4d5f58}
    .workspace-grid{display:grid;grid-template-columns:minmax(18rem,.75fr) minmax(24rem,1.25fr);gap:1.25rem;align-items:start}.panel-heading{display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid var(--ink);margin-bottom:1rem;padding-bottom:.7rem}.count{background:var(--red);color:#fff;border-radius:999px;min-width:2rem;padding:.35rem;text-align:center}
    .job-list{display:grid;gap:.5rem;list-style:none;margin:0;padding:0}.job-row{align-items:center;background:#f5f1e7;color:var(--ink);display:flex;justify-content:space-between;text-align:left;width:100%}.job-row>span:first-child{display:grid;gap:.2rem;min-width:0}.job-row code{font-size:.72rem;overflow-wrap:anywhere}.job-row.selected{border-left:7px solid var(--red);background:#fff4de}
    .status{border:1px solid currentColor;font:700 .7rem "Courier New",monospace;padding:.3rem;text-transform:uppercase}.status[data-status="completed"]{color:#12633f}.status[data-status="failed"]{color:#971b12}.status[data-status="running"]{color:#075e73}.status[data-status="cancelled"]{color:#5b5367}
    .empty-state{background:#f5f1e7;border:1px dashed #718078;padding:1rem}.job-detail{display:grid;gap:.8rem}.detail-status{display:flex;justify-content:space-between;align-items:center}.job-detail progress{accent-color:var(--teal);width:100%}.job-detail dl{display:grid;gap:.4rem;margin:0}.job-detail dl div{display:grid;grid-template-columns:6rem 1fr;gap:1rem;border-bottom:1px solid #d5d0c5;padding:.4rem 0}.job-detail dt{font-weight:700}.job-detail dd{margin:0;overflow-wrap:anywhere}.cancel{background:#fff;color:#8f1d14;border-color:#8f1d14;justify-self:start}
    .report{border-top:3px solid var(--ink);display:grid;gap:.7rem;margin-top:1.5rem;padding-top:1rem}.report-heading{display:flex;gap:1rem;justify-content:space-between}.report-heading code{font-size:.72rem;overflow-wrap:anywhere}.report-section{border-left:5px solid var(--teal);background:#f5f1e7;padding:.8rem}.report-section[data-section-status="unsupported"],.report-section[data-section-status="not_run"]{border-left-color:#b97713}.report-section[data-section-status="failed"]{border-left-color:#971b12}.report-section header{display:flex;justify-content:space-between}.report-section h4{margin:0}.report-section a{color:#0b4f3c;font-weight:700;text-decoration-line:underline;text-decoration-thickness:.14em;text-underline-offset:.2em}.report-section a:focus-visible{background:#fff4de;outline:3px solid #a33b20;outline-offset:3px}.report-section pre{background:var(--ink);color:#fff;max-height:22rem;overflow:auto;padding:.75rem;white-space:pre-wrap}
    @media(max-width:850px){.hero{align-items:start;flex-direction:column}.start-panel,.workspace-grid{grid-template-columns:1fr}.input-row{grid-template-columns:1fr}.report-heading{display:grid}}
    @media(prefers-reduced-motion:reduce){.loader{animation:none}}
  `],
})
export class ModelAnalysisShellComponent implements OnInit {
  readonly facade = inject(ModelAnalysisFacade);
  importRef = '';

  ngOnInit(): void {
    this.facade.loadOverview();
  }

  start(): void {
    this.facade.start(this.importRef);
  }

  canCancel(status: ModelAnalysisJobStatus): boolean {
    return isCancellableStatus(status);
  }

  statusLabel(status: ModelAnalysisJobStatus): string {
    return {
      queued: 'Wartend',
      claimed: 'Übernommen',
      running: 'Läuft',
      cancel_requested: 'Abbruch angefragt',
      completed: 'Abgeschlossen',
      failed: 'Fehlgeschlagen',
      cancelled: 'Abgebrochen',
      unknown: 'Unbekannt',
    }[status];
  }

  sectionStateText(status: string): string {
    return {
      unsupported: 'Diese Analyse wird für das Modell oder Runtimeprofil nicht unterstützt.',
      not_run: 'Dieser Abschnitt wurde nicht ausgeführt.',
      failed: 'Dieser Abschnitt ist fehlgeschlagen; der Reason-Code bleibt maßgeblich.',
    }[status] || 'Keine Daten verfügbar.';
  }

  formatData(value: unknown): string {
    try {
      const text = JSON.stringify(value, null, 2);
      return text.length > 12_000 ? `${text.slice(0, 12_000)}\n… [gekürzt]` : text;
    } catch {
      return '[nicht darstellbar]';
    }
  }
}
