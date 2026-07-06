import { Component, OnInit, inject } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { SystemFacade } from '../features/system/system.facade';
import { AgentApiService } from '../services/agent-api.service';

@Component({
  standalone: true,
  selector: 'app-classroom-assistant',
  imports: [FormsModule, JsonPipe],
  template: `
    <section class="card">
      <div class="toolbar">
        <h3>Classroom-Hilfen</h3>
        <input [(ngModel)]="roomFilter" placeholder="Zoom-Raum" />
        <input [(ngModel)]="moduleFilter" placeholder="Modul" />
        <button class="button-outline" (click)="load()">Aktualisieren</button>
      </div>
      <div class="layout">
        <div>
          @for (card of filteredCards(); track card.card_id) {
            <button class="item" (click)="selected = card">
              <span class="chip" [class]="'chip-' + card.status">{{ card.status }}</span>
              <strong>{{ card.question_summary }}</strong>
              <small>{{ card.zoom_room }} · {{ card.module || 'ungeklärt' }}</small>
            </button>
          } @empty { <p class="muted">Keine Karten vorhanden.</p> }
        </div>
        @if (selected) {
          <article class="detail">
            <h4>{{ selected.question_summary }}</h4>
            <p>{{ selected.answer?.answer_for_student || selected.answer?.next_action_for_teacher || 'Dozentenprüfung erforderlich.' }}</p>
            <h5>Material-Evidence</h5>
            <pre>{{ selected.evidence_refs | json }}</pre>
            <h5>Kontext-Hints</h5>
            <pre>{{ selected.context_hints | json }}</pre>
            @if (selected.workflow_part) {
              <h5>Workflow-Verifikation: {{ selected.workflow_part.verifier_status }}</h5>
              <pre>{{ selected.workflow_part.verifier_reasons | json }}</pre>
              <button class="button-outline" (click)="exportWorkflow()">Export kopieren</button>
            }
            <div class="actions">
              <button class="button-outline" (click)="copyAnswer()">Antwort kopieren</button>
              <button class="button-outline" (click)="setStatus('answered')">Beantwortet</button>
              <button class="button-outline" (click)="setStatus('dismissed')">Verwerfen</button>
            </div>
          </article>
        }
      </div>
    </section>
  `,
  styles: [`
    .toolbar,.actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .layout { display:grid; grid-template-columns:minmax(280px,1fr) 2fr; gap:16px; margin-top:12px; }
    .item { width:100%; display:grid; gap:4px; text-align:left; margin-bottom:8px; padding:10px; }
    .chip { width:max-content; border:1px solid var(--border); border-radius:10px; padding:2px 8px; font-size:11px; }
    .chip-open { color:#d99000; }.chip-answered { color:#2fa96b; }.chip-dismissed { opacity:.6; }
    pre { max-height:180px; overflow:auto; white-space:pre-wrap; }
    @media(max-width:800px){.layout{grid-template-columns:1fr}}
  `]
})
export class ClassroomAssistantComponent implements OnInit {
  private api = inject(AgentApiService);
  private system = inject(SystemFacade);
  cards: any[] = [];
  selected: any = null;
  roomFilter = '';
  moduleFilter = '';

  ngOnInit() { this.load(); }
  private hubUrl(): string | null { return this.system.resolveHubAgent()?.url || null; }
  load() {
    const url = this.hubUrl(); if (!url) return;
    this.api.getClassroomCards(url).subscribe((result: any) => this.cards = result?.items || []);
  }
  filteredCards() {
    return this.cards.filter(card =>
      (!this.roomFilter || card.zoom_room?.includes(this.roomFilter)) &&
      (!this.moduleFilter || card.module?.includes(this.moduleFilter)));
  }
  setStatus(status: string) {
    const url = this.hubUrl(); if (!url || !this.selected) return;
    this.api.updateClassroomCard(url, this.selected.card_id, status).subscribe((card: any) => {
      Object.assign(this.selected, card); this.load();
    });
  }
  copyAnswer() {
    const text = this.selected?.answer?.answer_for_student || this.selected?.answer?.next_action_for_teacher || '';
    void navigator.clipboard.writeText(text);
  }
  exportWorkflow() {
    const url = this.hubUrl(); if (!url || !this.selected) return;
    this.api.exportClassroomWorkflow(url, this.selected.card_id).subscribe((payload: any) =>
      void navigator.clipboard.writeText(JSON.stringify(payload, null, 2)));
  }
}
