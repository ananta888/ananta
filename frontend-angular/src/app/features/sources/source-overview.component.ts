import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { SourceControlCenterFacade } from './source-control-center.facade';
import { ProjectContextService } from '../../services/project-context.service';

@Component({
  selector: 'app-source-overview',
  standalone: true,
  imports: [RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="source-overview" aria-labelledby="source-overview-title">
      <header class="overview-head">
        <div>
          <p class="eyebrow">Source Control Center</p>
          <h2 id="source-overview-title">Quellen</h2>
          <p class="muted">Verbindung, Snapshot und realer CodeCompass-Index auf einen Blick.</p>
        </div>
        <div class="actions">
          <a class="btn" routerLink="journey" queryParamsHandling="preserve">Index-Journey</a>
          <a class="btn" routerLink="add" queryParamsHandling="preserve">Quelle hinzufügen</a>
          <button class="btn btn-secondary" type="button" (click)="facade.load()" [disabled]="facade.loading()">
            Neu laden
          </button>
        </div>
      </header>

      @switch (facade.viewState()) {
        @case ('loading') {
          <div class="state-panel" role="status" aria-live="polite">{{ facade.stateMessage() }}</div>
        }
        @case ('empty') {
          <div class="state-panel">
            <strong>{{ facade.stateMessage() }}</strong>
            <a class="btn" routerLink="add" queryParamsHandling="preserve">Erste Quelle hinzufügen</a>
          </div>
        }
        @case ('ready') {
          <div class="filters" aria-label="Quellen filtern">
            <label>
              Suche
              <input type="search" [value]="search()" (input)="setSearch($event)" placeholder="Name oder ID">
            </label>
            <label>
              Art
              <select [value]="typeFilter()" (change)="setTypeFilter($event)">
                <option value="">Alle Arten</option>
                @for (type of sourceTypes(); track type) {
                  <option [value]="type">{{ type }}</option>
                }
              </select>
            </label>
            <label>
              Zustand
              <select [value]="statusFilter()" (change)="setStatusFilter($event)">
                <option value="">Alle Zustände</option>
                <option value="enabled">Verbunden</option>
                <option value="disabled">Deaktiviert</option>
                <option value="indexed">Index vorhanden</option>
                <option value="unindexed">Ohne Index</option>
              </select>
            </label>
          </div>

          @if (filteredRows().length === 0) {
            <div class="state-panel">Keine Quelle entspricht den Filtern.</div>
          } @else {
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Art</th>
                    <th>Verbindung</th>
                    <th>Snapshot</th>
                    <th>CodeCompass</th>
                    <th>Profil</th>
                    <th>Aktionen</th>
                  </tr>
                </thead>
                <tbody>
                  @for (row of filteredRows(); track row.source.source_id) {
                    <tr>
                      <td>
                        <a [routerLink]="[row.source.source_id]" queryParamsHandling="preserve">
                          <strong>{{ row.source.display_name }}</strong>
                        </a>
                        <small>{{ row.source.source_id }}</small>
                      </td>
                      <td>{{ row.source.source_type }}</td>
                      <td>
                        <span class="status" [attr.data-state]="row.source.enabled ? 'ok' : 'muted'">
                          {{ row.source.enabled ? '● Verbunden' : '○ Deaktiviert' }}
                        </span>
                      </td>
                      <td>{{ row.source.latest_snapshot?.status || 'Noch kein Snapshot' }}</td>
                      <td>
                        <span class="status" [attr.data-state]="row.activeIndex ? 'ok' : 'warn'">
                          {{ row.activeIndex ? '● ' + row.activeIndex.status : '△ Nicht indiziert' }}
                        </span>
                        @if (row.activeIndex) {
                          <small>{{ row.activeIndex.knowledge_index_id }}</small>
                        }
                      </td>
                      <td>{{ row.activeIndex?.profile_name || 'Nicht verfügbar' }}</td>
                      <td>
                        <button
                          class="btn btn-secondary"
                          type="button"
                          (click)="facade.refreshSource(row.source.source_id)"
                          [disabled]="facade.mutating() || !row.nextActions.includes('refresh')">
                          Aktualisieren
                        </button>
                        <button
                          class="btn btn-secondary"
                          type="button"
                          (click)="facade.scanSource(row.source.source_id)"
                          [disabled]="facade.mutating() || !row.nextActions.includes('scan')">
                          Scannen
                        </button>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }

          @if (facade.nextCursor()) {
            <button
              class="btn btn-secondary"
              type="button"
              (click)="facade.loadMore()"
              [disabled]="facade.loading()"
            >Weitere Quellen laden</button>
          }
        }
        @default {
          <div class="state-panel error-state" role="alert">
            <strong>{{ facade.stateMessage() }}</strong>
            @if (facade.error()?.reasonCode) {
              <small>Referenz: {{ facade.error()!.reasonCode }}</small>
            }
            <button class="btn btn-secondary" type="button" (click)="facade.load()">Erneut versuchen</button>
          </div>
        }
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    .source-overview { display: grid; gap: 18px; }
    .overview-head, .actions, .filters { display: flex; align-items: end; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .eyebrow { margin: 0; color: var(--accent); font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
    h2 { margin: 3px 0; }
    .muted, small { color: var(--muted); }
    .filters { justify-content: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--card-bg); }
    label { display: grid; gap: 5px; min-width: 180px; font-size: 12px; font-weight: 600; }
    input, select { min-height: 36px; padding: 6px 9px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg); color: var(--fg); }
    .table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; }
    table { width: 100%; border-collapse: collapse; min-width: 940px; }
    th, td { padding: 11px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; background: var(--card-bg); }
    td strong, td small { display: block; }
    .status { font-weight: 650; }
    .status[data-state="ok"] { color: #087c56; }
    .status[data-state="warn"] { color: #9a5b00; }
    .status[data-state="muted"] { color: var(--muted); }
    .state-panel { min-height: 150px; display: grid; place-content: center; justify-items: center; gap: 12px; padding: 24px; border: 1px dashed var(--border); border-radius: 12px; text-align: center; }
    .error-state { border-color: #b91c1c; background: color-mix(in srgb, #b91c1c 7%, transparent); }
    .packs { border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
    .packs summary { cursor: pointer; font-weight: 650; }
    .pack-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-top: 12px; }
    .pack-grid article { display: grid; gap: 8px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
    @media (max-width: 700px) {
      .overview-head { align-items: stretch; }
      .actions { justify-content: flex-start; }
      label { width: 100%; }
    }
  `],
})
export class SourceOverviewComponent {
  readonly facade = inject(SourceControlCenterFacade);
  readonly projectContext = inject(ProjectContextService);
  readonly search = signal('');
  readonly typeFilter = signal('');
  readonly statusFilter = signal('');

  readonly sourceTypes = computed(() => Array.from(
    new Set(this.facade.rows().map(row => row.source.source_type)),
  ).sort());

  readonly filteredRows = computed(() => {
    const search = this.search().trim().toLowerCase();
    const type = this.typeFilter();
    const status = this.statusFilter();
    return this.facade.rows().filter(row => {
      const matchesSearch = !search
        || row.source.display_name.toLowerCase().includes(search)
        || row.source.source_id.toLowerCase().includes(search);
      const matchesType = !type || row.source.source_type === type;
      const matchesStatus = !status
        || (status === 'enabled' && row.source.enabled)
        || (status === 'disabled' && !row.source.enabled)
        || (status === 'indexed' && Boolean(row.activeIndex))
        || (status === 'unindexed' && !row.activeIndex);
      return matchesSearch && matchesType && matchesStatus;
    });
  });

  constructor() {
    effect(() => {
      if (this.projectContext.hasProject()) {
        this.facade.load();
      }
    });
  }

  setSearch(event: Event): void {
    this.search.set((event.target as HTMLInputElement).value);
  }

  setTypeFilter(event: Event): void {
    this.typeFilter.set((event.target as HTMLSelectElement).value);
  }

  setStatusFilter(event: Event): void {
    this.statusFilter.set((event.target as HTMLSelectElement).value);
  }
}
