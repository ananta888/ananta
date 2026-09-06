import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetApiService, MeetTurn } from './meet-api.service';

@Component({
  selector: 'app-meet-agent', standalone: true, imports: [FormsModule],
  template: `
    <section aria-label="Lokaler KI-Medienassistent">
      <h3>Lokaler KI-Assistent</h3>
      <p>Antworttext, synthetische deutsche Stimme und einfaches Avatarvideo werden lokal erzeugt.
        Ohne die folgende Zusatzfreigabe bleibt die Ausgabe eine lokale Vorschau.</p>
      <label><input type="checkbox" [(ngModel)]="publishToMeet" [disabled]="busy()" />
        Antwort als KI-Teilnehmer im zugeordneten Meet ausgeben (Hub-Vorautorisierung erforderlich)</label>
      <label>Nachricht an Ananta
        <textarea [(ngModel)]="prompt" maxlength="2000" [disabled]="busy()"></textarea>
      </label>
      <button type="button" (click)="send()" [disabled]="busy() || !prompt.trim()">Antwort erzeugen</button>
      <button type="button" (click)="clear()" [disabled]="busy()">Lokale Vorschau löschen</button>
      <p>Ein freigegebener Auftrag läuft begrenzt weiter. Abbruch über die Hub-Aufgabenverwaltung.</p>
      @if (busy()) { <p role="status">Lokale Antwort wird erzeugt (maximal zwei Minuten) …</p> }
      @if (error()) { <p role="alert">{{ error() }}</p> }
      @if (reply()) { <p aria-label="KI-Chatantwort">Ananta (KI): {{ reply() }}</p> }
      @if (published()) { <p>In Meet veröffentlicht; Empfang durch andere Teilnehmer nicht bestätigt.</p> }
      @if (videoUrl()) { <video [src]="videoUrl()" controls playsinline preload="metadata" aria-label="Synthetischer Ananta-Avatar"></video> }
    </section>
  `,
})
export class MeetAgentComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) projectId = '';
  @Input() taskId = '';
  private readonly api = inject(MeetApiService);
  private readonly auth = inject(UserAuthService);
  private request?: Subscription;
  private identity?: Subscription;
  prompt = '';
  publishToMeet = false;
  readonly busy = signal(false);
  readonly reply = signal('');
  readonly videoUrl = signal('');
  readonly error = signal('');
  readonly published = signal(false);

  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.clear()); }
  ngOnChanges(): void { this.clear(); }
  ngOnDestroy(): void { this.clear(); this.identity?.unsubscribe(); }

  clear(): void {
    this.request?.unsubscribe();
    this.request = undefined;
    if (this.videoUrl()) URL.revokeObjectURL(this.videoUrl());
    this.videoUrl.set(''); this.reply.set(''); this.error.set('');
    this.busy.set(false); this.prompt = '';
    this.publishToMeet = false; this.published.set(false);
  }

  send(): void {
    if (this.busy() || !this.projectId || !this.prompt.trim()) return;
    const text = this.prompt.trim();
    const publish = this.publishToMeet;
    this.clear(); this.busy.set(true);
    const request = this.taskId ? this.api.turn(this.projectId, text, publish, this.taskId)
      : publish ? this.api.turn(this.projectId, text, true) : this.api.turn(this.projectId, text);
    this.request = request.subscribe({
      next: value => {
        try { this.display(value); } catch { this.error.set('Ungültige Medienantwort.'); }
        this.busy.set(false);
      },
      error: failure => {
        this.busy.set(false);
        this.error.set(failure?.status === 403 ? 'Keine Hub-Vorautorisierung für dieses Projekt.'
          : failure?.status === 404 ? 'Lokaler Medienworker ist noch nicht aktiviert.'
          : 'Lokale Medienerzeugung fehlgeschlagen oder Zeitlimit erreicht.');
      },
    });
  }

  private display(value: MeetTurn): void {
    if (value.schema !== 'ananta.meet-turn-result.v1' || typeof value.text !== 'string'
        || value.text.length > 450 || value.video?.mime !== 'video/mp4'
        || typeof value.video.base64 !== 'string' || value.video.base64.length > 4_700_000) {
      throw new Error('meet_turn_result_invalid');
    }
    const bytes = Uint8Array.from(atob(value.video.base64), char => char.charCodeAt(0));
    this.videoUrl.set(URL.createObjectURL(new Blob([bytes], { type: 'video/mp4' })));
    this.reply.set(value.text);
    this.published.set(value.meeting?.status === 'published');
  }
}
