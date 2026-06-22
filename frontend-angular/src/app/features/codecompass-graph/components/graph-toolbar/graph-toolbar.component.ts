import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GraphFilter } from '../../models/graph-filter.model';
import { GraphNodeKind, GraphEdgeType } from '../../models/graph.model';
import { GraphViewMode, GRAPH_VIEW_MODES, GRAPH_VIEW_MODE_LABELS } from '../../models/graph-view-mode';
import { GraphLayoutMode, GRAPH_LAYOUT_MODES, GRAPH_LAYOUT_MODE_LABELS } from '../../models/graph-layout-mode';
import { ALL_NODE_KINDS, ALL_EDGE_TYPES } from '../../models/graph-filter.model';

@Component({
  standalone: true,
  selector: 'app-graph-toolbar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="toolbar">
      <div class="toolbar-group">
        @for (mode of viewModes; track mode) {
          @if (mode !== '3d' || webglAvailable) {
            <button
              class="mode-btn"
              [class.active]="activeMode === mode"
              (click)="viewModeChange.emit(mode)"
            >{{ modeLabels[mode] }}</button>
          }
        }
      </div>

      <div class="toolbar-group">
        <input
          class="search-input"
          type="search"
          placeholder="Search nodes…"
          [ngModel]="filter.searchText"
          (ngModelChange)="onSearch($event)"
        />
      </div>

      @if (activeMode === '2d') {
        <div class="toolbar-group">
          <label class="filter-label">Layout:</label>
          <select
            class="layout-select"
            [ngModel]="layoutMode"
            (ngModelChange)="layoutModeChange.emit($event)"
          >
            @for (mode of layoutModes; track mode) {
              <option [value]="mode">{{ layoutModeLabels[mode] }}</option>
            }
          </select>
        </div>
      }

      <div class="toolbar-group">
        <label class="filter-label">Kind:</label>
        <select
          multiple
          class="filter-select"
          [ngModel]="filter.nodeKindFilter"
          (ngModelChange)="onKindFilter($event)"
        >
          @for (kind of allNodeKinds; track kind) {
            <option [value]="kind">{{ kind }}</option>
          }
        </select>
      </div>

      <div class="toolbar-group">
        <label class="filter-label">Edge type:</label>
        <select
          multiple
          class="filter-select"
          [ngModel]="filter.edgeTypeFilter"
          (ngModelChange)="onEdgeFilter($event)"
        >
          @for (et of allEdgeTypes; track et) {
            <option [value]="et">{{ et }}</option>
          }
        </select>
      </div>

      @if (filter.nodeKindFilter.length || filter.edgeTypeFilter.length || filter.searchText) {
        <button class="reset-btn" (click)="filterReset.emit()">Clear filters</button>
      }
    </div>
  `,
  styles: [`
    .toolbar { display: flex; align-items: flex-start; gap: .75rem; flex-wrap: wrap; padding: .5rem; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }
    .toolbar-group { display: flex; align-items: center; gap: .35rem; }
    .mode-btn { padding: 3px 10px; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; cursor: pointer; font-size: .8rem; }
    .mode-btn.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }
    .search-input { padding: 3px 8px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: .85rem; width: 180px; }
    .layout-select { font-size: .8rem; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 6px; background: #fff; }
    .filter-label { font-size: .8rem; color: #555; white-space: nowrap; }
    .filter-select { font-size: .8rem; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 4px; height: 60px; }
    .reset-btn { padding: 3px 8px; border: 1px solid #f87171; background: #fff; color: #b91c1c; border-radius: 4px; cursor: pointer; font-size: .8rem; }
  `],
})
export class GraphToolbarComponent {
  @Input() activeMode: GraphViewMode = 'simple';
  @Input() layoutMode: GraphLayoutMode = 'tier';
  @Input() filter: GraphFilter = { searchText: '', nodeKindFilter: [], edgeTypeFilter: [] };
  @Input() webglAvailable = true;

  @Output() viewModeChange = new EventEmitter<GraphViewMode>();
  @Output() layoutModeChange = new EventEmitter<GraphLayoutMode>();
  @Output() filterChange = new EventEmitter<Partial<GraphFilter>>();
  @Output() filterReset = new EventEmitter<void>();

  readonly viewModes = GRAPH_VIEW_MODES;
  readonly modeLabels = GRAPH_VIEW_MODE_LABELS;
  readonly layoutModes = GRAPH_LAYOUT_MODES;
  readonly layoutModeLabels = GRAPH_LAYOUT_MODE_LABELS;
  readonly allNodeKinds = ALL_NODE_KINDS;
  readonly allEdgeTypes = ALL_EDGE_TYPES;

  onSearch(text: string): void {
    this.filterChange.emit({ searchText: text });
  }

  onKindFilter(kinds: GraphNodeKind[]): void {
    this.filterChange.emit({ nodeKindFilter: kinds });
  }

  onEdgeFilter(types: GraphEdgeType[]): void {
    this.filterChange.emit({ edgeTypeFilter: types });
  }
}
