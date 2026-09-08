import { DatePipe } from '@angular/common';
import { Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { ExplanationNoticeComponent } from '../../shared/ui/display/explanation-notice.component';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { BrowserCommand, MeetBrowserStatus } from './meet-browser-workspace';

@Component({ selector: 'app-meet-browser-workspace', standalone: true,
  imports: [FormsModule, DatePipe, ExplanationNoticeComponent], template: `
  <section aria-label="Bereinigte Browserpräsentation">
    <h5>Öffentliche Browser-Arbeitsansicht</h5>
    <app-explanation-notice tone="info" title="Bereinigte Textansicht, kein Desktop-Screenshot">
      <p>Nur ausdrücklich erlaubte öffentliche Dokumente, ohne Anmeldung, Skripte oder persönliche Tabs.
        Loginfelder und unbekannte aktive Inhalte stoppen die Ansicht. Kein semantischer Geheimnisschutz für beliebige Texte.</p>
    </app-explanation-notice>
    <p>Navigation startet einen eigenen Hub-Task für höchstens 30 Sekunden, zeigt ihn aber noch nicht im Raum.
      Präsentation separat auswählen; zusätzlich muss „Arbeitsansicht“ freigegeben sein. Raumteilnahme erteilt keine Steuerrechte.</p>
    <button type="button" (click)="load()" [disabled]="unavailable()">Browserzustand laden</button>
    @if (state(); as value) {
      <p>Auswahl: {{ value.mode === 'browser' ? 'bereinigter Browser' : value.mode === 'status' ? 'KI-Statusansicht' : 'keine Präsentation' }} · Revision {{ value.revision }}.</p>
      @if (value.task_id) {
        <p>Browser-Task: {{ value.task_id }} · {{ value.task_status }} · endet spätestens {{ value.deadline_ms | date:'medium' }}.</p>
      }
      <label>Öffentliche Dokument-URL
        <input type="url" maxlength="2048" autocomplete="off" spellcheck="false" [(ngModel)]="url" [disabled]="unavailable()" />
      </label>
      <button type="button" (click)="navigate()" [disabled]="unavailable() || !url || value.revision >= 1023">Dokument laden (noch nicht zeigen)</button>
      <button type="button" (click)="change('present')" [disabled]="unavailable() || value.task_status !== 'in_progress' || value.revision >= 1023">Browser präsentieren</button>
      <button type="button" (click)="change('stop')" [disabled]="unavailable() || value.revision >= 1023">Browser-Task und Präsentation stoppen</button>
      <button type="button" (click)="change('status')" [disabled]="unavailable() || value.revision >= 1023">KI-Statusansicht auswählen</button>
      <p>Auswahl und Taskstatus bestätigen keine sichtbare Zustellung. Bei Widerruf, Fehler oder Ablauf keine Ersatzquelle;
        erneutes Laden braucht einen neuen Hub-Task. Andere Audio-/Videoquellen bleiben unabhängig.</p>
    }
    @if (message()) { <p role="status">{{ message() }}</p> }
  </section>` })
export class MeetBrowserWorkspaceComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) projectId = ''; @Input({ required: true }) taskId = '';
  @Input({ required: true }) taskStatus = ''; @Input() disabled = false;
  private readonly api = inject(MeetDialogApiService); private readonly auth = inject(UserAuthService);
  private request?: Subscription; private identity?: Subscription;
  readonly state = signal<MeetBrowserStatus | null>(null); readonly busy = signal(false); readonly message = signal('');
  url = '';
  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.reset()); }
  ngOnChanges(changes: SimpleChanges): void {
    if (changes['projectId'] || changes['taskId'] || changes['taskStatus'] && this.taskStatus !== 'in_progress') this.reset();
  }
  ngOnDestroy(): void { this.reset(); this.identity?.unsubscribe(); }
  private reset(): void {
    this.request?.unsubscribe(); this.state.set(null); this.busy.set(false); this.message.set(''); this.url = '';
  }
  unavailable(): boolean { return this.disabled || this.busy() || !this.projectId || !this.taskId || this.taskStatus !== 'in_progress'; }
  load(): void {
    if (!this.unavailable()) this.send(this.api.browserStatus(this.projectId, this.taskId));
  }
  navigate(): void {
    const state = this.state();
    if (this.unavailable() || !state || !this.url || state.revision >= 1023) return;
    this.send(this.api.browserCommand(this.projectId, this.taskId, { action: 'navigate', expected_revision: state.revision, url: this.url }));
  }
  change(action: Exclude<BrowserCommand['action'], 'navigate'>): void {
    const state = this.state();
    if (this.unavailable() || !state || state.revision >= 1023 || action === 'present' && state.task_status !== 'in_progress') return;
    this.send(this.api.browserCommand(this.projectId, this.taskId, { action, expected_revision: state.revision }));
  }
  private send(operation: Observable<MeetBrowserStatus>): void {
    this.busy.set(true); this.message.set('');
    this.request = operation.subscribe({
      next: value => { this.state.set(value); this.busy.set(false); },
      error: () => {
        this.state.set(null); this.busy.set(false);
        this.message.set('Browserzustand nicht bestätigt oder nicht erlaubt. Bitte neu laden; ein angefragter Task kann bereits laufen. Keine automatische Wiederholung.');
      },
    });
  }
}
