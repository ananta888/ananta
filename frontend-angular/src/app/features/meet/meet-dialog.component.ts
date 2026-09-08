import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService, MeetDialog, DialogSource, optionalDialogSources } from './meet-dialog-api.service';
import { MeetAvatarPickerComponent } from './meet-avatar-picker.component';
import { MeetVoicePickerComponent } from './meet-voice-picker.component';
import { MeetDialogPhaseComponent } from './meet-dialog-phase.component';
import { MeetDialogDiagnosticsComponent } from './meet-dialog-diagnostics.component';
import { PendingDialogStart } from './meet-dialog-start-attempt';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';

const sourceCapabilities: Record<DialogSource, readonly string[]> = {
  chat: ['chat.read', 'chat.send'], audio: ['audio.receive', 'chat.send'], screen: ['screen.publish'], speech: ['speech.publish'],
  avatar: ['avatar.publish'],
};

@Component({ selector: 'app-meet-dialog', standalone: true, imports: [FormsModule, MeetAvatarPickerComponent, MeetVoicePickerComponent, MeetDialogPhaseComponent, MeetDialogDiagnosticsComponent], template: `
  <section aria-label="Autorisierter Meet-Dialog">
    <h3>Ananta im Raum</h3>
    <p>Der Hub startet einen isolierten KI-Teilnehmer. Zuhören und Chatlesen benötigen zusätzlich die
      Freigabe jedes jeweiligen Teilnehmers unter Meet → Analyse. Keine Aufzeichnung oder Tool-Freigabe.</p>
    <fieldset [disabled]="busy() || startPending()"><legend>Neue Startanfrage</legend>
    <label><input type="checkbox" [ngModel]="chat" (ngModelChange)="setChat($event)" [disabled]="busy()" />Auf neue Raumchat-Nachrichten antworten</label>
    <label><input type="checkbox" [ngModel]="speech" (ngModelChange)="setSpeech($event)" [disabled]="busy() || !chat" />Raumchat-Antworten zusätzlich mit lokaler KI-Stimme sprechen</label>
    <label><input type="checkbox" [(ngModel)]="voiceProfiles" [disabled]="busy() || !speech" />Zusätzlich freigegebene Persona-Stimmprofile und Stimmwechsel erlauben</label>
    <p>Sprachausgabe benötigt Raumchat und eine ausdrückliche Hub-Operatorfreigabe. Sie aktiviert kein Mikrofon
      und kein Zuhören; erkannte Audioeingaben werden in dieser Ausbaustufe weiterhin nur im Textchat beantwortet.</p>
    <label><input type="checkbox" [(ngModel)]="audio" [disabled]="busy()" />Freigegebenes Audio lokal erkennen und beantworten</label>
    <label><input type="checkbox" [(ngModel)]="screen" [disabled]="busy()" />Eigene isolierte KI-Arbeitsansicht teilen (kein Desktop)</label>
    <label><input type="checkbox" [ngModel]="avatar" (ngModelChange)="setAvatar($event)" [disabled]="busy()" />KI-Avatar erlauben (zunächst pausiert)</label>
    <label><input type="checkbox" [(ngModel)]="avatarImages" [disabled]="busy() || !avatar" />Zusätzlich freigegebene Persona-Profilbilder und Bildwechsel erlauben</label>
    <p>Das feste KI-Symbol ist keine Kameraaufnahme und kein ausgewähltes Persona-Bild. Nach dem Start separat
      über „KI-Avatar: fortsetzen“ aktivieren; Sprache und Bildschirm bleiben unabhängig.</p>
    <label>Antwortstrategie <select [(ngModel)]="mode" [disabled]="busy()">
      <option value="mention">Nur bei @ananta</option><option value="direct_question">Nur direkte Fragen an @ananta</option>
      <option value="room">Raumbeiträge im begrenzten Antwortbudget</option></select></label>
    <label>Laufzeit <select [(ngModel)]="minutes" [disabled]="busy()">
      <option [ngValue]="5">5 Minuten</option><option [ngValue]="15">15 Minuten</option>
      <option [ngValue]="60">1 Stunde</option><option [ngValue]="120">2 Stunden</option></select></label>
    <button type="button" (click)="start()" [disabled]="busy() || !(chat || audio || screen || avatar)">KI-Teilnehmer starten</button>
    </fieldset>
    @if (startPending()) {
      <p>Die Startanfrage ist noch nicht eindeutig bestätigt. Eine Wiederholung verwendet denselben Auftragsschlüssel
        und dieselbe Auswahl. Der ursprüngliche Auftrag kann bereits laufen.</p>
      <button type="button" (click)="retryStart()" [disabled]="busy()">Dieselbe Startanfrage wiederholen</button>
      <button type="button" (click)="prepareAnotherStart()" [disabled]="busy()">Andere Startanfrage vorbereiten (alte wird nicht gestoppt)</button>
    }
    <button type="button" (click)="reload()" [disabled]="busy()">Meine Aufträge aktualisieren</button>
    @if (message()) { <p role="status">{{ message() }}</p> }
    @for (item of dialogs(); track item.task_id) {
      <article><h4>Ananta (KI)</h4><p>Hub-Task: {{ item.task_id }} · {{ item.status }}</p>
        <p>Dies ist der Auftragsstatus, keine Bestätigung der Medienzustellung.</p>
        <app-meet-dialog-phase [projectId]="projectId" [taskId]="item.task_id" [taskStatus]="item.status"
          [controlRevision]="item.controls.revision" [disabled]="busy()" />
        <app-meet-dialog-diagnostics [projectId]="projectId" [taskId]="item.task_id" [taskStatus]="item.status"
          [disabled]="busy()" />
        @if (item.avatar_selection; as selection) {
          <p>Avatar-Auswahl: {{ selection.mode === 'neutral-ai-v1' ? 'festes KI-Symbol' : 'ausgewähltes Persona-Profilbild' }}.
            Die Auswahl ist keine aktuelle Publikationsfreigabe. Der Hub prüft sie erneut; bei Widerruf kein Ersatzbild.</p>
          <app-meet-avatar-picker [projectId]="projectId" [disabled]="busy() || item.status !== 'in_progress'"
            (avatarSelected)="selectAvatar(item, $event)" />
        }
        @if (item.voice_selection; as selection) {
          <p>Stimm-Auswahl: {{ selection.mode === 'configured-piper-v1' ? 'konfigurierte KI-Stimme' : 'ausgewähltes Persona-Stimmprofil' }}.
            Keine Bestätigung aktueller Freigabe oder Zustellung. Der Hub prüft das Stimmprofil erneut.</p>
          <app-meet-voice-picker [projectId]="projectId" [disabled]="busy() || item.status !== 'in_progress'"
            (voiceSelected)="selectVoice(item, $event)" />
        }
        @for (source of sourceNames; track source.key) {
          @if (item.controls[source.key]; as control) {
          <button type="button" [disabled]="busy() || item.status !== 'in_progress' || !canControl(item, source.key)"
            (click)="toggle(item, source.key)">{{ source.label }}: {{ control.enabled ? 'pausieren' : 'fortsetzen' }}</button>
          }
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
  private readonly startAttempt = new PendingDialogStart();
  readonly startPending = this.startAttempt.pending;
  readonly busy = signal(false); readonly message = signal(''); readonly dialogs = signal<MeetDialog[]>([]);
  readonly nextCursor = signal<number | null>(null);
  readonly sourceNames = [{ key: 'chat', label: 'Raumchat' }, { key: 'audio', label: 'Audioempfang' },
    { key: 'screen', label: 'Arbeitsansicht' }, { key: 'speech', label: 'Sprachausgabe' }, { key: 'avatar', label: 'KI-Avatar' }] as const;
  chat = false; audio = false; screen = false; speech = false; voiceProfiles = false; avatar = false; avatarImages = false; mode = 'mention'; minutes = 15;
  setChat(enabled: boolean): void { this.chat = enabled; if (!enabled) this.setSpeech(false); }
  setSpeech(enabled: boolean): void { this.speech = enabled; if (!enabled) this.voiceProfiles = false; }
  setAvatar(enabled: boolean): void { this.avatar = enabled; if (!enabled) this.avatarImages = false; }
  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.reset()); }
  ngOnChanges(): void { this.reset(); }
  ngOnDestroy(): void { this.request?.unsubscribe(); this.identity?.unsubscribe(); this.startAttempt.clear(); }
  private reset(): void {
    this.startAttempt.clear();
    this.request?.unsubscribe(); this.busy.set(false); this.dialogs.set([]); this.nextCursor.set(null); this.message.set('');
    this.chat = this.audio = this.screen = this.speech = this.voiceProfiles = this.avatar = this.avatarImages = false;
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
    if (this.busy() || this.startPending() || !this.projectId || !(this.chat || this.audio || this.screen || this.avatar)) return;
    if (this.speech && !this.chat) { this.message.set('Sprachausgabe benötigt ausdrücklich ausgewählten Raumchat.'); return; }
    if (this.voiceProfiles && !this.speech) { this.message.set('Stimmprofile benötigen ausdrücklich ausgewählte Sprachausgabe.'); return; }
    if (this.avatarImages && !this.avatar) { this.message.set('Profilbilder benötigen ausdrücklich ausgewählten KI-Avatar.'); return; }
    const capabilities = [...(this.chat ? ['chat.read'] : []), ...(this.chat || this.audio ? ['chat.send'] : []),
      ...(this.audio ? ['audio.receive'] : []), ...(this.screen ? ['screen.publish'] : []),
      ...(this.speech ? ['speech.publish'] : []), ...(this.avatar ? ['avatar.publish'] : [])];
    this.startAttempt.begin(this.api.start(this.projectId, this.taskId, { capabilities, duration_seconds: this.minutes * 60,
      chat_mode: this.chat || this.audio ? this.mode : 'off', audio_mode: this.audio ? 'dialog' : 'off',
      ...(this.avatarImages ? { avatar_images: true } : {}), ...(this.voiceProfiles ? { voice_profiles: true } : {}) }));
    this.retryStart();
  }
  retryStart(): void {
    const operation = this.startAttempt.current();
    if (this.busy() || !operation) return;
    this.busy.set(true); this.message.set('');
    this.request = operation.subscribe({
      next: () => { this.startAttempt.clear(); this.busy.set(false); this.reload(); },
      error: error => this.failure(error),
    });
  }
  prepareAnotherStart(): void {
    if (this.busy() || !this.startPending()) return;
    this.startAttempt.clear();
    this.message.set('Neue Startanfrage vorbereitet. Der ursprüngliche Auftrag kann weiterlaufen; bitte Aufträge prüfen.');
  }
  stop(item: MeetDialog): void {
    if (this.busy()) return; this.busy.set(true);
    this.request = this.api.stop(this.projectId, item.task_id).subscribe({ next: value => this.replace(value), error: error => this.failure(error) });
  }
  toggle(item: MeetDialog, source: DialogSource): void {
    if (this.busy() || item.status !== 'in_progress' || !this.canControl(item, source)) return; this.busy.set(true);
    const control = item.controls[source]!;
    const body = { expected_revision: item.controls.revision, chat: item.controls.chat.enabled,
      audio: item.controls.audio.enabled, screen: item.controls.screen.enabled,
      ...Object.fromEntries(optionalDialogSources.flatMap(name => item.controls[name] ? [[name, item.controls[name]!.enabled]] : [])),
      [source]: !control.enabled };
    this.request = this.api.control(this.projectId, item.task_id, body).subscribe({ next: value => this.replace(value), error: error => this.failure(error) });
  }
  canControl(item: MeetDialog, source: DialogSource): boolean {
    return Boolean(item.controls[source]) && sourceCapabilities[source].every(capability => item.capabilities.includes(capability));
  }
  selectAvatar(item: MeetDialog, profile: PersonaEffectiveProfile['selection'] | null): void {
    if (this.busy() || item.status !== 'in_progress' || !item.avatar_selection || !this.canControl(item, 'avatar')) return;
    this.busy.set(true); this.message.set('');
    const body = { expected_revision: item.controls.revision, ...(profile === null ? { neutral: true } : { profile }) };
    this.request = this.api.selectAvatar(this.projectId, item.task_id, body).subscribe({
      next: value => this.replace(value), error: error => this.failure(error),
    });
  }
  selectVoice(item: MeetDialog, profile: PersonaEffectiveProfile['selection'] | null): void {
    if (this.busy() || item.status !== 'in_progress' || !item.voice_selection || !this.canControl(item, 'speech')) return;
    this.busy.set(true); this.message.set('');
    const body = { expected_revision: item.controls.revision, ...(profile === null ? { configured: true } : { profile }) };
    this.request = this.api.selectVoice(this.projectId, item.task_id, body).subscribe({
      next: value => this.replace(value), error: error => this.failure(error),
    });
  }
  private replace(item: MeetDialog): void {
    this.dialogs.update(rows => rows.map(row => row.task_id === item.task_id ? item : row)); this.busy.set(false);
  }
}
