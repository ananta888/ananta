import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService, MeetDialog } from './meet-dialog-api.service';

@Component({ selector: 'app-meet-dialog', standalone: true, imports: [FormsModule], template: `
  <section aria-label="Autorisierter Meet-Dialog">
    <h3>Ananta im Raum</h3>
    <p>Der Hub startet einen isolierten KI-Teilnehmer. Zuhören und Chatlesen benötigen zusätzlich die
      Freigabe jedes jeweiligen Teilnehmers unter Meet → Analyse. Keine Aufzeichnung oder Tool-Freigabe.</p>
    <label><input type="checkbox" [(ngModel)]="chat" [disabled]="busy()" />Auf neue Raumchat-Nachrichten antworten</label>
    <label><input type="checkbox" [(ngModel)]="audio" [disabled]="busy()" />Freigegebenes Audio lokal erkennen und beantworten</label>
    <label><input type="checkbox" [(ngModel)]="screen" [disabled]="busy()" />Eigene isolierte KI-Arbeitsansicht teilen (kein Desktop)</label>
    <label>Antwortstrategie <select [(ngModel)]="mode" [disabled]="busy()">
      <option value="mention">Nur bei @ananta</option><option value="direct_question">Nur direkte Fragen an @ananta</option>
      <option value="room">Raumbeiträge im begrenzten Antwortbudget</option></select></label>
    <label>Laufzeit <select [(ngModel)]="minutes" [disabled]="busy()">
      <option [ngValue]="5">5 Minuten</option><option [ngValue]="15">15 Minuten</option>
      <option [ngValue]="60">1 Stunde</option><option [ngValue]="120">2 Stunden</option></select></label>
    <button type="button" (click)="start()" [disabled]="busy() || !(chat || audio || screen)">KI-Teilnehmer starten</button>
    <button type="button" (click)="reload()" [disabled]="busy()">Meine Aufträge aktualisieren</button>
    @if (message()) { <p role="status">{{ message() }}</p> }
    @for (item of dialogs(); track item.task_id) {
      <article><h4>Ananta (KI)</h4><p>Hub-Task: {{ item.task_id }} · {{ item.status }}</p>
        <p>Dies ist der Auftragsstatus, keine Bestätigung der Medienzustellung.</p>
        @for (source of sourceNames; track source.key) {
          <button type="button" [disabled]="busy() || item.status !== 'in_progress' || !canControl(item, source.key)"
            (click)="toggle(item, source.key)">{{ source.label }}: {{ item.controls[source.key].enabled ? 'pausieren' : 'fortsetzen' }}</button>
        }
        <button type="button" [disabled]="busy() || item.status !== 'in_progress'" (click)="stop(item)">KI-Auftrag vollständig stoppen</button>
      </article>
    }
    @if (nextCursor() !== null) { <button type="button" [disabled]="busy()" (click)="reload(nextCursor()!)">Weitere Aufträge</button> }
    <p>Ein gestarteter Auftrag läuft beim Schließen dieser Ansicht bis zum Stoppen oder Ablauf weiter.
      Die Ausbaustufe ist noch nicht gemeinsam produktiv abgenommen; fehlende Operatorpolicy wird nicht umgangen.</p>
  </section>` })
export class MeetDialogComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) projectId = ''; @Input() taskId = '';
  private readonly api = inject(MeetDialogApiService); private readonly auth = inject(UserAuthService);
  private request?: Subscription; private identity?: Subscription;
  readonly busy = signal(false); readonly message = signal(''); readonly dialogs = signal<MeetDialog[]>([]);
  readonly nextCursor = signal<number | null>(null);
  readonly sourceNames = [{ key: 'chat', label: 'Raumchat' }, { key: 'audio', label: 'Audioempfang' }, { key: 'screen', label: 'Arbeitsansicht' }] as const;
  chat = false; audio = false; screen = false; mode = 'mention'; minutes = 15;
  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.reset()); }
  ngOnChanges(): void { this.reset(); }
  ngOnDestroy(): void { this.request?.unsubscribe(); this.identity?.unsubscribe(); }
  private reset(): void {
    this.request?.unsubscribe(); this.busy.set(false); this.dialogs.set([]); this.nextCursor.set(null); this.message.set('');
    this.chat = this.audio = this.screen = false;
  }
  private failure(error: {status?: number}): void {
    this.busy.set(false);
    this.message.set(error?.status === 403 ? 'Keine passende Projekt-/Operatorfreigabe.'
      : error?.status === 404 ? 'Dialogbetrieb ist nicht aktiviert oder der Auftrag nicht zugänglich.'
      : error?.status === 409 ? 'Auftrag oder Quellenrechte wurden geändert. Bitte aktualisieren.'
      : 'Ergebnis unklar oder Verbindung gestört. Vor einem erneuten Start bitte die Aufträge aktualisieren.');
  }
  reload(cursor = 0): void {
    if (!this.projectId || this.busy()) return;
    this.busy.set(true); this.message.set('');
    this.request = this.api.list(this.projectId, cursor).subscribe({ next: value => {
      this.dialogs.set(value.items); this.nextCursor.set(value.next_cursor); this.busy.set(false);
    }, error: error => { this.dialogs.set([]); this.nextCursor.set(null); this.failure(error); } });
  }
  start(): void {
    if (this.busy() || !this.projectId || !(this.chat || this.audio || this.screen)) return;
    const capabilities = [...(this.chat ? ['chat.read'] : []), ...(this.chat || this.audio ? ['chat.send'] : []),
      ...(this.audio ? ['audio.receive'] : []), ...(this.screen ? ['screen.publish'] : [])];
    this.busy.set(true); this.message.set('');
    this.request = this.api.start(this.projectId, this.taskId, { capabilities, duration_seconds: this.minutes * 60,
      chat_mode: this.chat || this.audio ? this.mode : 'off', audio_mode: this.audio ? 'dialog' : 'off' }).subscribe({
      next: () => { this.busy.set(false); this.reload(); }, error: error => this.failure(error) });
  }
  stop(item: MeetDialog): void {
    if (this.busy()) return; this.busy.set(true);
    this.request = this.api.stop(this.projectId, item.task_id).subscribe({ next: value => this.replace(value), error: error => this.failure(error) });
  }
  toggle(item: MeetDialog, source: 'chat' | 'audio' | 'screen'): void {
    if (this.busy() || item.status !== 'in_progress' || !this.canControl(item, source)) return; this.busy.set(true);
    const body = { expected_revision: item.controls.revision, chat: item.controls.chat.enabled,
      audio: item.controls.audio.enabled, screen: item.controls.screen.enabled, [source]: !item.controls[source].enabled };
    this.request = this.api.control(this.projectId, item.task_id, body).subscribe({ next: value => this.replace(value), error: error => this.failure(error) });
  }
  canControl(item: MeetDialog, source: 'chat' | 'audio' | 'screen'): boolean {
    const needed = source === 'chat' ? ['chat.read', 'chat.send'] : source === 'audio'
      ? ['audio.receive', 'chat.send'] : ['screen.publish'];
    return needed.every(capability => item.capabilities.includes(capability));
  }
  private replace(item: MeetDialog): void {
    this.dialogs.update(rows => rows.map(row => row.task_id === item.task_id ? item : row)); this.busy.set(false);
  }
}
