import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, Subscription, finalize } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { MemoryAuditApiService } from './memory-audit-api.service';
import { MemoryAuditEvent, MemoryAuditFilters, MemoryAuditPage, MemoryDiff, MemoryDiffPage } from './memory-audit.models';

@Component({
  standalone: true,
  selector: 'app-memory-audit',
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section>
      <h2>Memory-Zugriffe</h2>
      <p>Erlaubte und abgelehnte Speicherzugriffe im ausgewählten Projekt. Projekt-Verwaltungsrechte sind erforderlich.</p>
      @if (!projectId) { <p>Bitte ein Projekt auswählen.</p> }
      <form (ngSubmit)="load()" class="filters">
        <label>Ansicht <select name="view" [(ngModel)]="view" (ngModelChange)="load()">
          <option value="events">Zugriffe</option><option value="diffs">Vorher/Nachher-Diffs</option>
        </select></label>
        <label>Run <input name="run" [(ngModel)]="filters.run_id" /></label>
        <label>Task <input name="task" [(ngModel)]="filters.task_id" /></label>
        <label>Trace <input name="trace" [(ngModel)]="filters.trace_id" /></label>
        <label>Kanal <input name="channel" [(ngModel)]="filters.channel_id" /></label>
        <label>Scope <select name="scope" [(ngModel)]="filters.scope">
          <option value="">Alle</option>
          @for (scope of scopes; track scope) { <option [value]="scope">{{ scope }}</option> }
        </select></label>
        <button type="submit" [disabled]="loading || !projectId">Anwenden / Aktualisieren</button>
      </form>
      @if (error) { <p role="alert">{{ error }}</p> }
      @if (loading) { <p role="status">Zugriffe werden geladen …</p> }
      @if (loaded) {
        @if (view === 'events') {
          <p>Die Ereignis-Hashes wurden geprüft. Eine vollständige Audit-Kettenprüfung ist hier nicht enthalten.</p>
          @if (!events.length) { <p>Keine Zugriffe für diese Auswahl.</p> }
        } @else {
          <p>Snapshot-Hashes und Audit-Verweise wurden geprüft. Die Diffs zeigen beobachtete Änderungen im freigegebenen Bereich; parallele Schreibvorgänge können darin enthalten sein.</p>
          <p>„deleted“ bedeutet: nicht mehr im erfassten Bereich vorhanden. Rohinhalte werden nicht angezeigt.</p>
          @if (!diffs.length) { <p>Für diesen Run sind keine Snapshots in der Auswahl vorhanden.</p> }
        }
      }
      <div class="events">
        @for (event of events; track event.event_id) {
          <article>
            <h3>{{ event.event_kind === 'memory_snapshot' ? 'Snapshot' : event.allowed ? 'Erlaubt' : 'Abgelehnt' }} · {{ event.operation }} · {{ event.channel_id }}</h3>
            <p>{{ event.reason_code }} · {{ event.created_at * 1000 | date:'medium' }}</p>
            <dl>
              <dt>Run / Task</dt><dd>{{ event.run_id }} / {{ event.namespace['task_id'] || '—' }}</dd>
              <dt>Actor / Trace</dt><dd>{{ event.actor_subject }} / {{ event.trace_id || '—' }}</dd>
              <dt>Scope / Sensitivität</dt><dd>{{ event.security.scope }} / {{ event.security.sensitivity }}</dd>
              <dt>Trust / Retention</dt><dd>{{ event.security.trust_level }} / {{ event.security.retention_class }}</dd>
              <dt>Policy-Revision</dt><dd>{{ event.policy_revision || '—' }}</dd>
              <dt>Audit-Aufbewahrung bis</dt><dd>{{ event.retention_until * 1000 | date:'medium' }}</dd>
            </dl>
            <details><summary>Ereignis und Prüfsummen</summary>
              <p>{{ event.event_id }}</p><p>{{ event.event_hash }}</p>
              <p>Vorheriger Hash: {{ event.previous_hash }}</p><p>Policy: {{ event.policy_digest || '—' }}</p>
            </details>
          </article>
        }
      </div>
      @for (diff of diffs; track diff.before_snapshot_id) {
        <article>
          <h3>{{ diff.channel_id }} · {{ diff.scope }}</h3>
          @if (diff.status === 'incomplete') { <p>Snapshot unvollständig: Nachher-Zustand fehlt.</p> }
          @else {
            <p>Speichersession: {{ diff.scope_outcome === 'failed' ? 'mit Fehler beendet' : 'beendet' }}</p>
            @if (!diff.changes.length) { <p>Keine Änderungen zwischen den Snapshots.</p> }
            @for (change of diff.changes; track change.entry_id) {
              <h4>{{ change.kind }} · {{ change.entry_id }}</h4>
              <dl>
                <dt>Inhalt vorher</dt><dd>{{ change.before?.content_digest || '—' }}</dd>
                <dt>Inhalt nachher</dt><dd>{{ change.after?.content_digest || '—' }}</dd>
                <dt>Metadaten vorher</dt><dd>{{ change.before?.metadata_digest || '—' }}</dd>
                <dt>Metadaten nachher</dt><dd>{{ change.after?.metadata_digest || '—' }}</dd>
              </dl>
            }
          }
          <details><summary>Snapshot- und Audit-Verweise</summary>
            <p>{{ diff.before_snapshot_id }}</p><p>{{ diff.after_snapshot_id || 'Nachher-Snapshot fehlt' }}</p>
            @for (eventId of diff.audit_event_ids; track eventId) { <p>{{ eventId }}</p> }
          </details>
        </article>
      }
      @if (nextAfterSequence !== null) {
        <button type="button" (click)="load(false)" [disabled]="loading">Weitere Zugriffe laden</button>
      }
    </section>
  `,
  styles: [`
    .filters{display:flex;flex-wrap:wrap;align-items:end;gap:1rem}
    label{display:grid;gap:.25rem}input,select{max-width:100%;box-sizing:border-box}
    article{border:1px solid #64748b;border-radius:8px;padding:1rem;margin:1rem 0}
    dl{display:grid;grid-template-columns:minmax(7rem,1fr) minmax(0,3fr);gap:.5rem}
    dd{margin:0;overflow-wrap:anywhere}details p{overflow-wrap:anywhere}
    @media(max-width:600px){dl{grid-template-columns:1fr}dd{margin-bottom:.5rem}}
  `],
})
export class MemoryAuditComponent implements OnInit, OnDestroy {
  private readonly state = inject(ControlCenterStateFacade);
  private readonly api = inject(MemoryAuditApiService);
  private readonly cdr = inject(ChangeDetectorRef);
  private projectSubscription?: Subscription;
  private requestSubscription?: Subscription;
  private activeFilters: MemoryAuditFilters = {};
  projectId = '';
  filters: MemoryAuditFilters = {};
  events: MemoryAuditEvent[] = [];
  diffs: MemoryDiff[] = [];
  view: 'events' | 'diffs' = 'events';
  nextAfterSequence: number | null = null;
  loading = false;
  loaded = false;
  error = '';
  readonly scopes = ['run', 'task', 'goal', 'conversation', 'workspace', 'global'];

  ngOnInit(): void {
    this.projectSubscription = this.state.selectedProjectId$.subscribe(projectId => {
      this.requestSubscription?.unsubscribe();
      this.projectId = projectId;
      this.filters = {};
      this.events = [];
      this.diffs = [];
      this.nextAfterSequence = null;
      this.loaded = false;
      this.error = '';
      if (projectId) this.load();
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void {
    this.projectSubscription?.unsubscribe();
    this.requestSubscription?.unsubscribe();
  }

  load(reset = true): void {
    this.requestSubscription?.unsubscribe();
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl || !this.projectId) { this.error = 'Hub oder Projekt nicht verfügbar'; return; }
    if (!reset && this.nextAfterSequence === null) return;
    if (reset) {
      this.activeFilters = Object.fromEntries(Object.entries(this.filters)
        .filter(([, value]) => value?.trim()).map(([key, value]) => [key, value?.trim()]));
      this.events = [];
      this.diffs = [];
      this.nextAfterSequence = null;
      this.loaded = false;
    }
    this.loading = true;
    this.error = '';
    const projectId = this.projectId;
    const expectedSchema = this.view === 'diffs' ? 'ananta.memory-diff-page.v1' : 'ananta.memory-audit-page.v1';
    const expectedIntegrity = this.view === 'diffs' ? 'snapshot_and_audit_links_verified' : 'event_digests_verified';
    if (this.view === 'diffs' && !this.activeFilters.run_id) {
      this.loading = false;
      this.error = 'Für einen Diff bitte einen Run angeben.';
      return;
    }
    const response: Observable<MemoryAuditPage | MemoryDiffPage> = this.view === 'diffs'
      ? this.api.diffs(hubUrl, projectId, this.activeFilters)
      : this.api.events(hubUrl, projectId, this.activeFilters, reset ? 0 : this.nextAfterSequence!);
    this.requestSubscription = response.pipe(
      finalize(() => { this.loading = false; this.cdr.markForCheck(); }),
    ).subscribe({
      next: page => {
        if (page.project_id !== this.projectId || page.schema !== expectedSchema || page.integrity !== expectedIntegrity) {
          this.events = [];
          this.diffs = [];
          this.nextAfterSequence = null;
          this.loaded = false;
          this.error = 'Audit-Antwort passt nicht zum Projekt oder ist nicht geprüft.';
          return;
        }
        if (page.schema === 'ananta.memory-diff-page.v1') {
          if (page.run_id !== this.activeFilters.run_id) {
            this.error = 'Diff-Antwort passt nicht zum Run.';
            return;
          }
          this.diffs = page.items;
          this.nextAfterSequence = null;
        } else {
          this.events = [...new Map([...this.events, ...page.items].map(event => [event.event_id, event])).values()];
          this.nextAfterSequence = page.next_after_sequence;
        }
        this.loaded = true;
      },
      error: () => { this.error = 'Audit-Zugriffe konnten nicht geladen werden. Projekt-Verwaltungsrechte und Hub-Verfügbarkeit prüfen.'; },
    });
  }
}
