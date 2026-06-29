import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, ActivatedRoute } from '@angular/router';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseFlowCase, CaseEvent, CaseArtifact, CaseAction } from '../caseflow.models';
import { ArtifactsTabComponent } from './artifacts-tab.component';
import { FitAnalysisTabComponent } from './fit-analysis-tab.component';
import { CommunicationTabComponent } from './communication-tab.component';
import { TraceTabComponent } from './trace-tab.component';
import { FlowTabComponent } from './flow-tab.component';

type Tab = 'overview' | 'posting' | 'evaluation' | 'documents' | 'communication' | 'timeline' | 'trace' | 'flow';

@Component({
  standalone: true,
  selector: 'app-application-detail',
  imports: [
    CommonModule,
    RouterModule,
    ArtifactsTabComponent,
    FitAnalysisTabComponent,
    CommunicationTabComponent,
    TraceTabComponent,
    FlowTabComponent,
  ],
  template: `
    <div class="detail">
      @if (loading()) {
        <p>Lade Bewerbung...</p>
      } @else if (case_()) {
        <div class="header">
          <div>
            <h2>{{ case_()!.title }}</h2>
            <span class="status">{{ case_()!.status }}</span>
            <span class="priority {{ case_()!.priority }}">{{ case_()!.priority }}</span>
          </div>
          <a routerLink="../board">← Zurück</a>
        </div>

        <div class="tabs">
          @for (tab of tabs; track tab.id) {
            <button [class.active]="activeTab() === tab.id" (click)="activeTab.set(tab.id)">
              {{ tab.label }}
            </button>
          }
        </div>

        <div class="tab-content">
          @switch (activeTab()) {
            @case ('overview') {
              <div class="overview">
                <dl>
                  <dt>Titel</dt><dd>{{ case_()!.title }}</dd>
                  <dt>Status</dt><dd>{{ case_()!.status }}</dd>
                  <dt>Priorität</dt><dd>{{ case_()!.priority }}</dd>
                  <dt>Erstellt</dt><dd>{{ case_()!.created_at | date:'medium' }}</dd>
                </dl>
              </div>
            }
            @case ('documents') {
              <app-artifacts-tab [caseId]="caseId()" />
            }
            @case ('evaluation') {
              <app-fit-analysis-tab [caseId]="caseId()" />
            }
            @case ('communication') {
              <app-communication-tab [caseId]="caseId()" />
            }
            @case ('timeline') {
              <div class="timeline">
                @for (evt of events(); track evt.id) {
                  <div class="event">
                    <span class="event-type">{{ evt.event_type }}</span>
                    <span class="event-title">{{ evt.title }}</span>
                    <span class="event-time">{{ evt.created_at | date:'short' }}</span>
                  </div>
                }
              </div>
            }
            @case ('trace') {
              <app-trace-tab [caseId]="caseId()" />
            }
            @case ('flow') {
              <app-flow-tab [caseId]="caseId()" />
            }
            @default {
              <p>Tab nicht verfügbar</p>
            }
          }
        </div>
      } @else {
        <p>Bewerbung nicht gefunden.</p>
      }
    </div>
  `,
  styles: [`
    .detail { padding: 1rem; }
    .header { display: flex; justify-content: space-between; margin-bottom: 1rem; }
    .status { background: #333; padding: 0.2rem 0.6rem; border-radius: 4px; margin-right: 0.5rem; }
    .priority.critical { color: #ef4444; } .priority.high { color: #f59e0b; }
    .tabs { display: flex; gap: 0.25rem; border-bottom: 1px solid #333; margin-bottom: 1rem; }
    .tabs button { background: none; border: none; color: #aaa; padding: 0.5rem 1rem; cursor: pointer; }
    .tabs button.active { color: #fff; border-bottom: 2px solid #60a5fa; }
    .overview dl { display: grid; grid-template-columns: 120px 1fr; gap: 0.5rem; }
    .overview dt { color: #aaa; }
    .timeline { display: flex; flex-direction: column; gap: 0.5rem; }
    .event { display: flex; gap: 1rem; padding: 0.5rem; background: #1e1e1e; border-radius: 4px; font-size: 0.85rem; }
    .event-type { color: #60a5fa; min-width: 140px; }
    .event-time { color: #666; margin-left: auto; }
  `],
})
export class ApplicationDetailComponent implements OnInit {
  private readonly api = inject(CaseFlowApiService);
  private readonly route = inject(ActivatedRoute);

  tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Überblick' },
    { id: 'posting', label: 'Stellenanzeige' },
    { id: 'evaluation', label: 'Bewertung' },
    { id: 'documents', label: 'Dokumente' },
    { id: 'communication', label: 'Kommunikation' },
    { id: 'timeline', label: 'Timeline' },
    { id: 'trace', label: 'Trace' },
    { id: 'flow', label: 'Flow' },
  ];

  loading = signal(true);
  case_ = signal<CaseFlowCase | null>(null);
  events = signal<CaseEvent[]>([]);
  activeTab = signal<Tab>('overview');

  caseId = signal('');

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    this.caseId.set(id);
    this.api.getCase(id).subscribe({
      next: (c) => { this.case_.set(c); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
    this.api.getTimeline(id).subscribe({
      next: (evts) => this.events.set(evts),
      error: () => {},
    });
  }
}
