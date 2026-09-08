import { DatePipe } from '@angular/common';
import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { MeetDialogApiService } from './meet-dialog-api.service';
import { DialogStopReason, MeetDialogDiagnostics } from './meet-dialog-diagnostics';

@Component({ selector: 'app-meet-dialog-diagnostics', standalone: true, imports: [DatePipe], template: `
  <section aria-label="Optionale Abschlussdiagnose">
    <button type="button" (click)="load()" [disabled]="disabled || busy()">Abschlussdiagnose laden</button>
    @if (diagnostics(); as value) {
      @if (value.observation_status === 'missing') {
        <p>Keine Abschlussdiagnose gespeichert. Das ist keine Bestätigung eines erfolgreichen Laufs.</p>
      } @else if (value.observation; as observation) {
        <p>Nicht verifizierte Worker-Meldung: {{ reasons[observation.stop_reason] }}.</p>
        <p>Hub-Status bei Eingang: {{ value.hub_task_status }} · Steuerrevision {{ value.hub_control_revision_at_receipt }}.
          Eingegangen: {{ value.recorded_at_ms | date:'medium' }}.</p>
        @if (observation.measurements; as metrics) {
          <dl>
            <dt>Gemessene Laufzeit</dt><dd>{{ metrics.elapsed_ms }} ms</dd>
            <dt>CPU-Zeit des Dialogprozesses</dt><dd>{{ metrics.self_cpu_ms }} ms</dd>
            <dt>CPU-Zeit beendeter Kindprozesse</dt><dd>{{ metrics.terminated_children_cpu_ms }} ms</dd>
            <dt>Maximaler RSS des Dialogprozesses</dt><dd>{{ metrics.self_max_rss_kib }} KiB</dd>
            <dt>Maximaler RSS beendeter Kindprozesse</dt><dd>{{ metrics.terminated_child_max_rss_kib }} KiB</dd>
          </dl>
          <p>Getrennte Prozessmessungen, kein gleichzeitiger Gesamtspeicher und keine GPU-Messung.</p>
        } @else { <p>Keine Ressourcenmesswerte verfügbar.</p> }
        <p>Keine Freigabe-Evidenz und keine Bestätigung zugestellter, sichtbarer oder hörbarer Medien.</p>
      }
    }
    @if (message()) { <p role="status">{{ message() }}</p> }
  </section>` })
export class MeetDialogDiagnosticsComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) projectId = ''; @Input({ required: true }) taskId = '';
  @Input({ required: true }) taskStatus = ''; @Input() disabled = false;
  private readonly api = inject(MeetDialogApiService); private readonly auth = inject(UserAuthService);
  private request?: Subscription; private identity?: Subscription;
  readonly diagnostics = signal<MeetDialogDiagnostics | null>(null); readonly busy = signal(false); readonly message = signal('');
  readonly reasons: Record<DialogStopReason, string> = {
    assignment_elapsed: 'zugewiesene Laufzeit beendet', control_stale: 'Steuerzustand nicht mehr frisch',
    hub_unavailable_or_revoked: 'Hub nicht erreichbar oder Freigabe entzogen',
    session_failed: 'Sitzungsfehler', runtime_failed: 'Laufzeitfehler',
  };
  ngOnInit(): void { this.identity = this.auth.user$.pipe(skip(1)).subscribe(() => this.reset()); }
  ngOnChanges(): void { this.reset(); }
  ngOnDestroy(): void { this.reset(); this.identity?.unsubscribe(); }
  private reset(): void {
    this.request?.unsubscribe(); this.busy.set(false); this.diagnostics.set(null); this.message.set('');
  }
  load(): void {
    if (this.disabled || this.busy() || !this.projectId || !this.taskId) return;
    this.diagnostics.set(null); this.message.set(''); this.busy.set(true);
    this.request = this.api.diagnostics(this.projectId, this.taskId).subscribe({
      next: value => { this.diagnostics.set(value); this.busy.set(false); },
      error: () => {
        this.diagnostics.set(null); this.busy.set(false);
        this.message.set('Abschlussdiagnose nicht verfügbar. Die normalen Auftragsfunktionen bleiben unabhängig davon.');
      },
    });
  }
}
