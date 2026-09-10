import { Component, Input, OnChanges, OnDestroy, inject, signal } from '@angular/core';
import { Subscription, catchError, defaultIfEmpty, defer, finalize, from, map, mergeMap, of, take, timeout } from 'rxjs';

import { AgentApiService } from '../services/agent-api.service';
import { SectionCardComponent } from '../shared/ui/layout/section-card.component';
import { StatusBadgeComponent } from '../shared/ui/state/status-badge.component';
import { ExplanationNoticeComponent } from '../shared/ui/display/explanation-notice.component';
import { PI_EXPECTED_VERSION, PiReadinessView, piReadinessUnavailable, piReadinessView } from './pi-backend-readiness';

export interface PiWorkerTarget { name: string; url: string; status: string }
interface PiWorkerRow { url: string; name: string; view: PiReadinessView }

@Component({
  standalone: true,
  selector: 'app-pi-backend-status',
  imports: [SectionCardComponent, StatusBadgeComponent, ExplanationNoticeComponent],
  template: `
    <app-section-card title="Pi Coding Agent" subtitle="Open Source / BYOK · Hub-gesteuerte Native-Aufträge">
      <button section-actions class="button-outline" [disabled]="busy() || !hubUrl" (click)="refresh()">
        Pi-Status aktualisieren
      </button>
      <app-explanation-notice tone="info" title="Konfiguration ist kein Inferenznachweis"
        message="Modell und Zugang werden vom Hub-Auftragsprofil vorgegeben. Ollama, LM Studio und OpenRouter
          sind konfigurierbar; Inferenzkosten hängen vom Anbieter ab. Diese Anzeige startet keine Modellanfrage." />
      <p class="muted">Erwartete Version: {{ expectedVersion }}. Headless, strukturierte Ausgabe;
        keine Tools, kein MCP, keine Workspace-Schreibrechte, keine Sitzungsfortsetzung.
        Kein globales Auto-Routing. Jede Ausführung benötigt eine Hub-Zuweisung und eine gültige Richtlinie.</p>
      <div aria-live="polite" [attr.aria-busy]="busy()">
        @for (row of rows(); track row.url) {
          <section class="pi-worker" [attr.aria-label]="row.name">
            <strong>{{ row.name }}</strong>
            <app-status-badge [label]="row.view.label" [tone]="row.view.tone" />
            <p>Version: {{ row.view.version }} · Authentifizierung: {{ row.view.auth }}</p>
          </section>
        } @empty {
          <p>{{ hubUrl ? 'Keine registrierten Worker gefunden.' : 'Kein Hub verfügbar.' }}</p>
        }
      </div>
      @if (truncated()) { <p>Die Statusabfrage ist auf 32 unterschiedliche Worker begrenzt.</p> }
    </app-section-card>
  `,
  styles: [`.pi-worker { padding: .75rem 0; border-bottom: 1px solid var(--border); }
    .pi-worker strong { margin-right: .75rem; } .pi-worker p { margin-bottom: 0; }`],
})
export class PiBackendStatusComponent implements OnChanges, OnDestroy {
  @Input() hubUrl = '';
  @Input() workers: readonly PiWorkerTarget[] = [];
  readonly expectedVersion = PI_EXPECTED_VERSION;
  readonly rows = signal<readonly PiWorkerRow[]>([]);
  readonly busy = signal(false);
  readonly truncated = signal(false);
  private readonly api = inject(AgentApiService);
  private request = new Subscription();
  private generation = 0;

  ngOnChanges(): void { this.refresh(); }
  ngOnDestroy(): void { this.generation++; this.request.unsubscribe(); }

  refresh(): void {
    const generation = ++this.generation;
    this.request.unsubscribe();
    this.busy.set(false);
    const hubUrl = this.hubUrl;
    const unique = [...new Map(this.workers.filter(worker => worker.url).map(worker => [worker.url, worker])).values()];
    this.truncated.set(unique.length > 32);
    const targets = hubUrl ? unique.slice(0, 32) : [];
    this.rows.set(targets.map(worker => ({
      url: worker.url, name: worker.name.slice(0, 80),
      view: piReadinessUnavailable(worker.status === 'online' ? 'Status wird abgefragt' : 'Worker nicht online'),
    })));
    const online = targets.filter(worker => worker.status === 'online');
    if (!online.length) return;
    this.busy.set(true);
    this.request = from(online).pipe(
      mergeMap(worker => defer(() => this.api.sgptBackendProvision(hubUrl, 'pi', {
        worker_url: worker.url, action: 'status',
      })).pipe(
        timeout(15000), take(1), defaultIfEmpty(null),
        map(response => ({ url: worker.url, view: piReadinessView(response, worker.url) })),
        catchError(() => of({ url: worker.url, view: piReadinessUnavailable('Statusabfrage fehlgeschlagen') })),
      ), 4),
      finalize(() => { if (generation === this.generation) this.busy.set(false); }),
    ).subscribe(result => {
      if (generation !== this.generation) return;
      this.rows.update(rows => rows.map(row => row.url === result.url ? { ...row, view: result.view } : row));
    });
  }
}
