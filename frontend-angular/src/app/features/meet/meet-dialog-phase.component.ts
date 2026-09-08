import { DatePipe } from '@angular/common';
import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { MeetDialogPhase } from './meet-dialog-phase';

@Component({ selector: 'app-meet-dialog-phase', standalone: true, imports: [DatePipe], template: `
  <section aria-label="Gespeicherte Sitzungsphase">
    <button type="button" (click)="load()" [disabled]="disabled || busy()">Sitzungsphase laden</button>
    <button type="button" (click)="load(true)" [disabled]="disabled || busy() || taskStatus !== 'in_progress'">
      Quellenzustand jetzt abfragen</button>
    @if (phase(); as value) {
      <p>Zuletzt gespeicherte Phase: {{ labels[value.phase] }} · Revision {{ value.revision }}.</p>
      @if (value.observed_at !== null) {
        <p>Registrierte Quellen bei der letzten Beobachtung: {{ value.registered_sources.join(', ') || 'keine' }}.
          Zeitpunkt: {{ value.observed_at | date:'medium' }}.</p>
        <p>{{ value.observation_fresh ? 'Bei der Abfrage war diese Beobachtung frisch.' : 'Bei der Abfrage lag keine frische Quellenbeobachtung vor.' }}</p>
      }
      <p>Historische Hub-/Meet-Beobachtung, keine laufende Liveanzeige und keine Bestätigung decodierter oder hörbarer Medien.</p>
    }
    @if (message()) { <p role="status">{{ message() }}</p> }
  </section>` })
export class MeetDialogPhaseComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) projectId = ''; @Input({ required: true }) taskId = '';
  @Input({ required: true }) taskStatus = ''; @Input() disabled = false;
  @Input() controlRevision = 0;
  private readonly api = inject(MeetDialogApiService); private readonly auth = inject(UserAuthService);
  private request?: Subscription; private identity?: Subscription;
  readonly phase = signal<MeetDialogPhase | null>(null); readonly busy = signal(false); readonly message = signal('');
  readonly labels: Record<MeetDialogPhase['phase'], string> = {
    queued: 'eingereiht', admitted: 'zugelassen', connecting: 'Verbindungsaufbau', joined: 'beigetreten',
    publishing: 'Quellen registriert', stopping: 'wird beendet', completed: 'abgeschlossen', failed: 'fehlgeschlagen', cancelled: 'abgebrochen',
  };
  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.reset()); }
  ngOnChanges(): void { this.reset(); }
  ngOnDestroy(): void { this.reset(); this.identity?.unsubscribe(); }
  private reset(): void {
    this.request?.unsubscribe(); this.busy.set(false); this.phase.set(null); this.message.set('');
  }
  load(refresh = false): void {
    if (this.disabled || this.busy() || !this.projectId || !this.taskId || refresh && this.taskStatus !== 'in_progress') return;
    this.phase.set(null); this.message.set(''); this.busy.set(true);
    this.request = this.api.phase(this.projectId, this.taskId, refresh).subscribe({
      next: value => { this.phase.set(value); this.busy.set(false); },
      error: () => {
        this.phase.set(null); this.busy.set(false);
        this.message.set('Keine verifizierbare Sitzungsphase verfügbar. Die normalen Auftragsfunktionen bleiben unabhängig davon.');
      },
    });
  }
}
