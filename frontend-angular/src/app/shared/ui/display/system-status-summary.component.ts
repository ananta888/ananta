import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { StatusBadgeComponent, StatusTone } from '../state';

export interface SystemStatusTeamMember {
  agentUrl: string;
  roleName: string;
}

@Component({
  standalone: true,
  selector: 'app-system-status-summary',
  imports: [CommonModule, RouterLink, StatusBadgeComponent],
  template: `
    <div class="card">
      <h3>System Status</h3>
      <div class="row gap-sm">
        <div
          class="status-dot"
          [class.online]="systemStatus === 'ok'"
          [class.offline]="systemStatus !== 'ok'"
          role="status"
          [attr.aria-label]="'Systemstatus ' + systemStatus"
        ></div>
        <app-status-badge [label]="systemStatus" [tone]="systemTone" [dot]="true"></app-status-badge>
      </div>

      <div class="muted font-sm mt-sm">
        Live Sync:
        <strong [class.success]="liveConnected" [class.danger]="!liveConnected">
          {{ liveConnected ? 'connected' : 'idle' }}
        </strong>
      </div>
      <div class="muted font-sm mt-sm">
        Task Snapshot:
        <strong [class.success]="!tasksLoading" [class.danger]="!!taskCollectionError">
          {{ tasksLoading ? 'loading' : 'signal-backed' }}
        </strong>
        @if (tasksLastLoadedAt) {
          <span> · Stand: {{ tasksLastLoadedAt * 1000 | date:'HH:mm:ss' }}</span>
        }
      </div>
      @if (lastSystemEventType) {
        <div class="muted font-sm mt-sm">
          Letztes Event: <strong>{{ lastSystemEventType }}</strong>
        </div>
      }
      @if (queueDepth !== null) {
        <div class="muted font-sm mt-sm">
          Queue-Tiefe: <strong>{{ queueDepth || 0 }}</strong>
        </div>
      }
      @if (registrationEnabled) {
        <div class="muted font-sm mt-sm">
          Registration: <strong>{{ registrationStatus }}</strong>
          @if (registrationAttempts) {
            <span> · Attempts: {{ registrationAttempts }}</span>
          }
        </div>
      }
      @if (schedulerKnown) {
        <div class="muted font-sm mt-sm">
          Scheduler: <strong>{{ schedulerRunning ? 'running' : 'stopped' }}</strong>
          <span> · Jobs: {{ schedulerJobCount || 0 }}</span>
        </div>
      }
      @if (contractsVersion) {
        <div class="muted font-sm mt-sm">
          Contracts: <strong>{{ contractsVersion }}</strong>
          <span> · Schemas: {{ contractsSchemaCount || 0 }}</span>
        </div>
        @if (taskStates.length) {
          <div class="muted status-text-sm mt-sm">
            Task-States: {{ taskStates.join(', ') }}
          </div>
        }
      }
      @if (activeTeamName) {
        <div class="muted font-sm mt-md">
          Aktives Team: <strong>{{ activeTeamName }}</strong> ({{ teamMembers.length }} Agenten)
          @if (teamMembers.length) {
            <div class="mt-sm">
              @for (member of teamMembers; track member.agentUrl + member.roleName) {
                <div class="status-text-sm font-sm">
                  {{ member.agentUrl }} - {{ member.roleName }}
                </div>
              }
            </div>
          }
        </div>
      } @else {
        <div class="muted font-sm mt-md">
          Kein Team aktiv.
        </div>
      }
      <div class="muted status-text-sm">
        Hub: {{ hubName }}<br>
        Letztes Update: {{ timestamp * 1000 | date:'HH:mm:ss' }}
      </div>
      <div class="mt-lg">
        <button [routerLink]="['/board']" class="w-full">Zum Task-Board</button>
      </div>
    </div>
  `,
})
export class SystemStatusSummaryComponent {
  @Input() systemStatus = 'unknown';
  @Input() systemTone: StatusTone = 'unknown';
  @Input() liveConnected = false;
  @Input() tasksLoading = false;
  @Input() taskCollectionError: string | null = null;
  @Input() tasksLastLoadedAt: number | null = null;
  @Input() lastSystemEventType = '';
  @Input() queueDepth: number | null = null;
  @Input() registrationEnabled = false;
  @Input() registrationStatus = '';
  @Input() registrationAttempts = 0;
  @Input() schedulerKnown = false;
  @Input() schedulerRunning = false;
  @Input() schedulerJobCount = 0;
  @Input() contractsVersion = '';
  @Input() contractsSchemaCount = 0;
  @Input() taskStates: string[] = [];
  @Input() activeTeamName = '';
  @Input() teamMembers: SystemStatusTeamMember[] = [];
  @Input() hubName = '';
  @Input() timestamp = 0;
}
