import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { CdkDragDrop, DragDropModule } from '@angular/cdk/drag-drop';
import { FormsModule } from '@angular/forms';

import { DashboardFeatureFlagStore } from '../features/dashboard-foundation/dashboard-feature-flags';
import { KanbanCard, KanbanColumnId } from '../features/tasks/kanban/kanban-api.client';
import { KanbanStore } from '../features/tasks/kanban/kanban.store';

@Component({
  standalone: true,
  selector: 'app-board',
  providers: [KanbanStore],
  imports: [CommonModule, FormsModule, DragDropModule],
  template: `
    @if (features.angularKanban()) {
      <main class="kanban-shell" aria-labelledby="kanban-title" data-testid="kanban-board">
        <header class="kanban-hero">
          <div>
            <p class="kanban-eyebrow">Hub Task Projection</p>
            <h1 id="kanban-title">Arbeitsfluss</h1>
            <p>Serverseitige Reihenfolge, Revisionen und Abhängigkeiten ohne zweite Task-Quelle.</p>
          </div>
          <button type="button" class="button-outline" (click)="store.reloadSnapshot()">
            Snapshot aktualisieren
          </button>
        </header>

        <section class="kanban-controls" aria-label="Board und Filter">
          <label>Board
            <select
              [ngModel]="store.board()?.id || ''"
              (ngModelChange)="store.selectBoard($event)"
              data-testid="kanban-board-select">
              @for (board of store.boards(); track board.id) {
                <option [value]="board.id">{{ board.name }} ({{ board.card_count }})</option>
              }
            </select>
          </label>
          <label class="search-control">Suche
            <input
              type="search"
              [(ngModel)]="searchText"
              (ngModelChange)="store.search($event)"
              placeholder="Titel oder Beschreibung"
              aria-describedby="search-help">
            <small id="search-help">Die Suche startet nach 300 ms auf dem Server.</small>
          </label>
          <label>Status
            <select
              [ngModel]="store.filters().column_id || ''"
              (ngModelChange)="setColumnFilter($event)">
              <option value="">Alle Status</option>
              <option value="todo">Offen</option>
              <option value="in_progress">In Arbeit</option>
              <option value="blocked">Blockiert</option>
              <option value="completed">Abgeschlossen</option>
            </select>
          </label>
          <label>Blockierung
            <select
              [ngModel]="blockedFilter"
              (ngModelChange)="setBlockedFilter($event)">
              <option value="">Alle</option>
              <option value="true">Nur blockiert</option>
              <option value="false">Nicht blockiert</option>
            </select>
          </label>
          <label>Zuständige ID
            <input
              [(ngModel)]="assigneeFilter"
              (change)="store.setFilter({ assignee_id: assigneeFilter.trim() || undefined })">
          </label>
        </section>

        @if (store.error()) {
          <p class="kanban-alert" role="alert">{{ store.error() }}</p>
        }
        @if (store.loading()) {
          <p class="kanban-loading" role="status">Board-Snapshot wird geladen …</p>
        }

        <section class="create-card" aria-labelledby="create-card-title">
          <h2 id="create-card-title">Karte anlegen</h2>
          <label>Titel
            <input [(ngModel)]="newTitle" maxlength="500">
          </label>
          <label>Beschreibung
            <input [(ngModel)]="newDescription" maxlength="20000">
          </label>
          <button
            type="button"
            [disabled]="!newTitle.trim()"
            (click)="createCard()">
            Karte anlegen
          </button>
        </section>

        @if (store.board(); as board) {
          <p class="snapshot-meta">
            Revision <code>{{ board.revision }}</code>
            <span aria-hidden="true">·</span>
            {{ board.card_count }} Karten
          </p>
          <section class="kanban-grid" aria-label="Kanban-Spalten">
            @for (column of board.columns; track column.id; let columnIndex = $index) {
              <section
                class="kanban-column"
                cdkDropList
                [id]="column.id"
                [cdkDropListData]="store.cardsFor(column.id)"
                [cdkDropListConnectedTo]="dropListIds()"
                (cdkDropListDropped)="drop($event, column.id)"
                [attr.aria-labelledby]="'column-' + column.id">
                <header>
                  <h2 [id]="'column-' + column.id">{{ column.title }}</h2>
                  <span class="column-count">{{ store.cardsFor(column.id).length }} angezeigt</span>
                </header>
                <div class="card-list">
                  @for (card of store.cardsFor(column.id); track card.id) {
                    <article
                      class="kanban-card"
                      cdkDrag
                      [cdkDragData]="card"
                      [attr.aria-label]="card.title + ', Status ' + statusLabel(card.column_id)">
                      <button class="card-title" type="button" (click)="store.openDetail(card)">
                        {{ card.title }}
                      </button>
                      <p class="card-description">{{ card.description || 'Keine Beschreibung' }}</p>
                      <div class="card-meta">
                        <span class="status-token" [attr.data-status]="card.column_id">
                          {{ statusLabel(card.column_id) }}
                        </span>
                        <span>Priorität {{ card.priority }}</span>
                        <span>Revision {{ card.revision }}</span>
                      </div>
                      @if (card.blocked) {
                        <p class="blocked-note"><strong>Blockiert:</strong> Klärung erforderlich</p>
                      }
                      <div class="keyboard-move" aria-label="Karte per Tastatur verschieben">
                        <button
                          type="button"
                          [disabled]="columnIndex === 0"
                          (click)="moveRelative(card, columnIndex - 1)">
                          Nach links
                        </button>
                        <button
                          type="button"
                          [disabled]="columnIndex === board.columns.length - 1"
                          (click)="moveRelative(card, columnIndex + 1)">
                          Nach rechts
                        </button>
                      </div>
                    </article>
                  }
                  @if (!store.cardsFor(column.id).length) {
                    <p class="empty-column">Keine Karten in diesem gefilterten Snapshot.</p>
                  }
                </div>
              </section>
            }
          </section>
        }

        @if (store.selectedCard(); as card) {
          <div class="detail-backdrop" (click)="store.closeDetail()" aria-hidden="true"></div>
          <aside
            class="card-detail"
            role="dialog"
            aria-modal="true"
            aria-labelledby="card-detail-title"
            data-testid="kanban-card-detail">
            <header>
              <div>
                <p class="kanban-eyebrow">Karte {{ card.id }}</p>
                <h2 id="card-detail-title">{{ card.title }}</h2>
              </div>
              <button type="button" (click)="store.closeDetail()" aria-label="Kartendetails schließen">
                Schließen
              </button>
            </header>
            <p>{{ card.description || 'Keine Beschreibung hinterlegt.' }}</p>
            <dl class="detail-facts">
              <div><dt>Status</dt><dd>{{ statusLabel(card.column_id) }}</dd></div>
              <div><dt>Revision</dt><dd>{{ card.revision }}</dd></div>
              <div><dt>Kommentare</dt><dd>{{ card.comment_count }}</dd></div>
              <div><dt>Aktivitäten</dt><dd>{{ card.activity_count }}</dd></div>
            </dl>

            <section aria-labelledby="dependencies-title">
              <h3 id="dependencies-title">Abhängigkeiten</h3>
              <label>Karten-IDs, durch Komma getrennt
                <input [(ngModel)]="dependencyText">
              </label>
              <button type="button" (click)="saveDependencies()">Abhängigkeiten speichern</button>
            </section>

            <section aria-labelledby="comments-title">
              <h3 id="comments-title">Kommentare</h3>
              <label>Neuer Kommentar
                <textarea [(ngModel)]="commentText" maxlength="10000"></textarea>
              </label>
              <button type="button" [disabled]="!commentText.trim()" (click)="addComment()">
                Kommentar speichern
              </button>
              <ol class="timeline">
                @for (comment of store.comments(); track comment.id) {
                  <li><strong>{{ comment.author_id }}</strong><p>{{ comment.body }}</p></li>
                }
              </ol>
            </section>

            <section aria-labelledby="activity-title">
              <h3 id="activity-title">Aktivität</h3>
              <ol class="timeline">
                @for (event of store.activity(); track event.id) {
                  <li>
                    <strong>{{ activityLabel(event.event_type) }}</strong>
                    <p>{{ event.message }}</p>
                  </li>
                }
              </ol>
            </section>
          </aside>
        }
      </main>
    }
  `,
  styles: [`
    :host { --ink: #152018; --paper: #f5f2e8; --accent: #dd5b36; --line: #c9c2ae; display: block; }
    .kanban-shell { color: var(--ink); display: grid; gap: 1rem; }
    .kanban-hero {
      background: linear-gradient(120deg, #193b2b, #315a3c 68%, #d6a43d);
      border-radius: 1.2rem; color: #fff; display: flex; justify-content: space-between;
      gap: 1rem; padding: clamp(1.25rem, 3vw, 2.5rem); box-shadow: 0 18px 45px #193b2b26;
    }
    .kanban-hero h1 { font-family: Georgia, serif; font-size: clamp(2rem, 5vw, 4rem); margin: 0; }
    .kanban-eyebrow { font-size: .72rem; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
    .kanban-controls, .create-card {
      align-items: end; background: var(--paper); border: 1px solid var(--line); border-radius: 1rem;
      display: grid; gap: .8rem; grid-template-columns: repeat(5, minmax(9rem, 1fr)); padding: 1rem;
    }
    .create-card { grid-template-columns: 1.2fr 2fr auto; }
    .create-card h2 { grid-column: 1 / -1; margin: 0; font-size: 1rem; }
    label { display: grid; gap: .35rem; font-weight: 700; }
    label small { font-weight: 400; }
    input, select, textarea { min-height: 2.75rem; }
    .snapshot-meta { color: #58625b; margin: 0; }
    .kanban-grid { display: grid; gap: .85rem; grid-template-columns: repeat(4, minmax(0, 1fr)); overflow-x: auto; }
    .kanban-column {
      background: #ebe6d8; border: 1px solid var(--line); border-radius: 1rem; min-width: 15rem;
      padding: .8rem; box-shadow: inset 0 3px 0 #193b2b;
    }
    .kanban-column > header { align-items: baseline; display: flex; justify-content: space-between; }
    .kanban-column h2 { font-family: Georgia, serif; font-size: 1.25rem; }
    .column-count { font-size: .75rem; }
    .card-list { display: grid; gap: .7rem; min-height: 5rem; }
    .kanban-card { background: #fffdf7; border: 1px solid #d8d0bc; border-radius: .8rem; padding: .85rem; }
    .kanban-card:focus-within { outline: 3px solid #1677c8; outline-offset: 2px; }
    .card-title { background: none; border: 0; color: var(--ink); font: 700 1rem/1.25 Georgia, serif; padding: 0; text-align: left; }
    .card-description { color: #58625b; font-size: .86rem; }
    .card-meta { display: flex; flex-wrap: wrap; gap: .35rem; font-size: .72rem; }
    .card-meta span { border: 1px solid #bcb5a4; border-radius: 99rem; padding: .2rem .45rem; }
    .status-token::before { content: '● '; }
    .status-token[data-status="todo"]::before { color: #686f77; }
    .status-token[data-status="in_progress"]::before { color: #0b6da7; }
    .status-token[data-status="blocked"]::before { color: #a32922; }
    .status-token[data-status="completed"]::before { color: #167044; }
    .blocked-note { background: #fff0eb; border-left: 4px solid #a32922; padding: .45rem; }
    .keyboard-move { display: flex; gap: .4rem; margin-top: .7rem; }
    .keyboard-move button { font-size: .72rem; padding: .35rem .5rem; }
    .empty-column { border: 1px dashed #98917f; border-radius: .6rem; color: #59615b; padding: .8rem; }
    .kanban-alert { background: #fff0eb; border-left: 5px solid #a32922; padding: .8rem; }
    .kanban-loading { background: #e8f2eb; padding: .8rem; }
    .detail-backdrop { background: #13231bb3; inset: 0; position: fixed; z-index: 9998; }
    .card-detail {
      background: #fffdf7; border: 0; box-shadow: -16px 0 50px #0004; inset: 0 0 0 auto;
      max-width: 38rem; overflow-y: auto; padding: 1.2rem; position: fixed; width: min(92vw, 38rem); z-index: 9999;
    }
    .card-detail > header { align-items: start; display: flex; justify-content: space-between; }
    .detail-facts { display: grid; grid-template-columns: repeat(2, 1fr); gap: .6rem; }
    .detail-facts div { background: var(--paper); padding: .6rem; }
    dt { font-size: .72rem; font-weight: 800; text-transform: uppercase; } dd { margin: .2rem 0 0; }
    .card-detail section { border-top: 1px solid var(--line); margin-top: 1rem; padding-top: 1rem; }
    .timeline { border-left: 2px solid var(--line); display: grid; gap: .7rem; padding-left: 1.2rem; }
    .timeline li::marker { color: var(--accent); }
    button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
      outline: 3px solid #1677c8; outline-offset: 2px;
    }
    @media (max-width: 900px) {
      .kanban-grid { grid-template-columns: repeat(4, minmax(17rem, 1fr)); }
      .kanban-controls { grid-template-columns: repeat(2, 1fr); }
      .create-card { grid-template-columns: 1fr; }
    }
    @media (max-width: 480px) {
      .kanban-hero { align-items: start; flex-direction: column; }
      .kanban-controls { grid-template-columns: 1fr; }
      .kanban-grid { scroll-snap-type: x mandatory; }
      .kanban-column { scroll-snap-align: start; width: 82vw; }
      .detail-facts { grid-template-columns: 1fr; }
    }
  `],
})
export class BoardComponent implements OnInit {
  readonly features = inject(DashboardFeatureFlagStore);
  readonly store = inject(KanbanStore);
  searchText = '';
  assigneeFilter = '';
  blockedFilter = '';
  newTitle = '';
  newDescription = '';
  commentText = '';
  dependencyText = '';

  ngOnInit(): void {
    this.features.ensureLoaded().subscribe(flags => {
      if (flags.angularKanban) this.store.load();
    });
  }

  drop(event: CdkDragDrop<readonly KanbanCard[]>, columnId: KanbanColumnId): void {
    const card = event.item.data as KanbanCard;
    if (card) this.store.move(card, columnId, event.currentIndex);
  }

  dropListIds(): string[] {
    return (this.store.board()?.columns ?? []).map(column => column.id);
  }

  moveRelative(card: KanbanCard, index: number): void {
    const column = this.store.board()?.columns[index];
    if (column) this.store.move(card, column.id, this.store.cardsFor(column.id).length);
  }

  setColumnFilter(value: string): void {
    this.store.setFilter({ column_id: value as KanbanColumnId || undefined });
  }

  setBlockedFilter(value: string): void {
    this.blockedFilter = value;
    this.store.setFilter({ blocked: value === '' ? undefined : value === 'true' });
  }

  createCard(): void {
    this.store.create(this.newTitle, this.newDescription);
    this.newTitle = '';
    this.newDescription = '';
  }

  addComment(): void {
    this.store.addComment(this.commentText);
    this.commentText = '';
  }

  saveDependencies(): void {
    this.store.updateDependencies(this.dependencyText);
  }

  statusLabel(status: KanbanColumnId): string {
    return ({
      todo: 'Offen',
      in_progress: 'In Arbeit',
      blocked: 'Blockiert',
      completed: 'Abgeschlossen',
    })[status];
  }

  activityLabel(value: string): string {
    return value.replace(/^kanban_/, '').replaceAll('_', ' ');
  }
}
