import { Component, Input, OnChanges, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, skip } from 'rxjs';
import { UserAuthService } from '../../services/user-auth.service';
import { SummaryPanelComponent } from '../../shared/ui/display/summary-panel.component';
import { MeetApiService, MeetBinding } from './meet-api.service';
import { MeetAgentComponent } from './meet-agent.component';

@Component({
  selector: 'app-meet-panel', standalone: true,
  imports: [FormsModule, SummaryPanelComponent, MeetAgentComponent],
  template: `
    <app-summary-panel title="ANANTA Meet" summary="Separater Meeting-Dienst für diesen Projekt- oder Task-Kontext.">
      @if (busy()) { <p role="status">Meeting-Zuordnung wird geladen …</p> }
      @if (message()) { <p role="status">{{ message() }}</p> }
      @if (binding(); as current) {
        <p>In Meet anmelden und einen privaten Raum erstellen. Anschließend den Einladungslink hier zuordnen.</p>
        <a [href]="current.profile.create_url" target="_blank" rel="noopener noreferrer">Raum in Meet erstellen</a>
        @if (current.invite_url) {
          <p><a [href]="current.invite_url" target="_blank" rel="noopener noreferrer">Zugeordnetes Meeting öffnen</a></p>
          <button type="button" [disabled]="busy()" (click)="unlink()">Zuordnung entfernen</button>
        }
        <p>Projektberechtigte können diesen Link nutzen. Meet prüft Anmeldung und Gerät separat.
          Entfernen der Zuordnung widerruft keine bereits geteilte Einladung.</p>
        <label>Meet-Einladungslink
          <input type="url" [ngModel]="invite()" (ngModelChange)="invite.set($event)"
            [disabled]="busy()" maxlength="512" autocomplete="off" spellcheck="false" />
        </label>
        <button type="button" [disabled]="busy() || !invite()" (click)="attach()">Raum zuordnen</button>
        <p>Die Zuordnung bestätigt weder Raumexistenz noch Medienverbindung. Kamera und Mikrofon bleiben in Meet steuerbar.</p>
        <app-meet-agent [projectId]="projectId" [taskId]="taskId" />
      }
      <button type="button" [disabled]="busy()" (click)="reload()">Aktualisieren</button>
    </app-summary-panel>
  `,
})
export class MeetPanelComponent implements OnChanges, OnDestroy, OnInit {
  @Input({ required: true }) projectId = '';
  @Input() taskId = '';
  private readonly api = inject(MeetApiService);
  private readonly auth = inject(UserAuthService);
  private request?: Subscription;
  private identitySubscription?: Subscription;
  readonly binding = signal<MeetBinding | null>(null);
  readonly invite = signal('');
  readonly busy = signal(false);
  readonly message = signal('');

  ngOnChanges(): void { this.reload(); }
  ngOnInit(): void {
    this.identitySubscription = this.auth.user$.pipe(skip(1)).subscribe(() => this.reload());
  }
  ngOnDestroy(): void { this.request?.unsubscribe(); this.identitySubscription?.unsubscribe(); }

  reload(): void {
    this.request?.unsubscribe();
    this.binding.set(null);
    this.invite.set('');
    if (!this.projectId) { this.busy.set(false); return; }
    this.execute('GET');
  }

  attach(): void {
    if (!this.busy() && this.binding()) this.execute('PUT', {
      expected_revision: this.binding()!.revision, invite_url: this.invite(),
    });
  }

  unlink(): void {
    if (!this.busy() && this.binding()) this.execute('DELETE', {
      expected_revision: this.binding()!.revision,
    });
  }

  private execute(method: 'GET' | 'PUT' | 'DELETE', body?: unknown): void {
    this.busy.set(true);
    this.message.set('');
    this.request = this.api.binding(this.projectId, this.taskId, method, body).subscribe({
      next: value => { this.binding.set(value); this.invite.set(''); this.busy.set(false); },
      error: error => {
        this.busy.set(false);
        // Remove stale capabilities on denial, failure or concurrent change.
        this.binding.set(null);
        this.invite.set('');
        this.message.set(error?.status === 404
          ? 'Meet ist deaktiviert oder dieser Kontext nicht verfügbar.'
          : error?.status === 409 ? 'Zuordnung wurde geändert. Bitte aktualisieren.'
          : error?.status === 403 || error?.status === 401 ? 'Für diese Aktion fehlt die Berechtigung.'
          : 'Meet-Zuordnung nicht möglich. Bitte Link und Verbindung prüfen.');
      },
    });
  }
}
