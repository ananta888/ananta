import {
  Component, Input, Output, EventEmitter, OnChanges, ChangeDetectionStrategy, signal, computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { GraphFilter } from '../../models/graph-filter.model';
import { GraphNodeKind, GraphEdgeType } from '../../models/graph.model';
import { GraphViewMode, GRAPH_VIEW_MODES, GRAPH_VIEW_MODE_LABELS } from '../../models/graph-view-mode';
import { GraphLayoutMode, GRAPH_LAYOUT_MODES, GRAPH_LAYOUT_MODE_LABELS } from '../../models/graph-layout-mode';
import { ALL_NODE_KINDS, ALL_EDGE_TYPES } from '../../models/graph-filter.model';

// ── Edge-type groups ──────────────────────────────────────────────────────────

interface EdgeGroup { label: string; types: GraphEdgeType[]; }
interface NodeGroup { label: string; kinds: GraphNodeKind[]; }

const EDGE_GROUPS: EdgeGroup[] = [
  { label: 'Aufrufe',            types: ['calls_probable_target', 'returns'] },
  { label: 'Importe',            types: ['imports_module', 'imports_symbol'] },
  { label: 'Enthält / Deklariert', types: [
      'child_of_type', 'child_of_file', 'parent_child',
      'contains_entry', 'contains_method', 'contains_section', 'contains_symbol', 'contains_type',
      'declares_constructor', 'declares_method', 'declares_bean',
    ],
  },
  { label: 'Vererbung',          types: ['extends', 'implements'] },
  { label: 'Typ-Nutzung',        types: [
      'field_type_uses', 'generic_type_uses',
      'method_param_type_uses', 'method_return_type_uses', 'uses_type',
    ],
  },
  { label: 'Framework / DI',     types: [
      'bean_factory_method', 'controller_endpoint_declares',
      'injects_dependency', 'jpa_relation', 'transactional_boundary',
    ],
  },
  { label: 'Sonstiges',          types: ['related'] },
];

const NODE_GROUPS: NodeGroup[] = [
  { label: 'Python',    kinds: ['python_class', 'python_function', 'python_method', 'python_module_summary', 'python_file', 'python_import'] },
  { label: 'TypeScript', kinds: [
      'typescript_class', 'typescript_function', 'typescript_method',
      'typescript_interface', 'typescript_type', 'typescript_const', 'typescript_enum',
      'typescript_folder_summary', 'typescript_file', 'typescript_constructor', 'typescript_import',
    ],
  },
  { label: 'Java',      kinds: ['java_method', 'java_type', 'java_file', 'java_constructor', 'java_constructor_detail', 'java_method_detail', 'java_module_summary'] },
  { label: 'Konfig / Sonstige', kinds: ['md_file', 'md_section', 'xml_tag', 'xml_file', 'xml_node_detail', 'yaml_file', 'yaml_entry', 'properties_file', 'properties_entry', 'config', 'unknown'] },
];

@Component({
  standalone: true,
  selector: 'app-graph-toolbar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="toolbar">

      <!-- ── View-mode buttons ── -->
      <div class="toolbar-group">
        @for (mode of viewModes; track mode) {
          @if (mode !== '3d' || webglAvailable) {
            <button class="mode-btn" [class.active]="activeMode === mode"
                    (click)="viewModeChange.emit(mode)">{{ modeLabels[mode] }}</button>
          }
        }
      </div>

      <!-- ── Search ── -->
      <div class="toolbar-group">
        <input class="search-input" type="search" placeholder="Knoten suchen…"
               [ngModel]="filter.searchText" (ngModelChange)="onSearch($event)" />
      </div>

      <!-- ── Layout (2D only) ── -->
      @if (activeMode === '2d') {
        <div class="toolbar-group">
          <label class="filter-label">Layout:</label>
          <select class="layout-select" [ngModel]="layoutMode" (ngModelChange)="layoutModeChange.emit($event)">
            @for (mode of layoutModes; track mode) {
              <option [value]="mode">{{ layoutModeLabels[mode] }}</option>
            }
          </select>
        </div>
      }

      <!-- ── Edge-type filter button ── -->
      <div class="toolbar-group filter-group" style="position:relative">
        <button class="filter-btn" [class.active]="edgeOpen()"
                (click)="edgeOpen.set(!edgeOpen())">
          Kanten&nbsp;<span class="filter-count">{{ edgeCountLabel() }}</span>
          <span class="chevron">{{ edgeOpen() ? '▲' : '▾' }}</span>
        </button>

        @if (edgeOpen()) {
          <div class="filter-panel" (click)="$event.stopPropagation()">
            <div class="panel-header">
              <span class="panel-title">Kantentypen</span>
              <div class="panel-actions">
                <button class="act-btn" (click)="setAllEdges(true)">Alle</button>
                <button class="act-btn" (click)="setAllEdges(false)">Keine</button>
                <button class="act-btn close-act" (click)="edgeOpen.set(false)">✕</button>
              </div>
            </div>
            <div class="panel-body">
              @for (group of edgeGroups; track group.label) {
                <div class="group">
                  <label class="group-header">
                    <input type="checkbox"
                           [checked]="isEdgeGroupAllChecked(group)"
                           [indeterminate]="isEdgeGroupPartial(group)"
                           (change)="toggleEdgeGroup(group, $any($event.target).checked)" />
                    <strong>{{ group.label }}</strong>
                  </label>
                  <div class="group-items">
                    @for (t of group.types; track t) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isEdgeChecked(t)"
                               (change)="toggleEdge(t, $any($event.target).checked)" />
                        <span class="cb-label">{{ t }}</span>
                      </label>
                    }
                  </div>
                </div>
              }
            </div>
          </div>
        }
      </div>

      <!-- ── Node-kind filter button ── -->
      <div class="toolbar-group filter-group" style="position:relative">
        <button class="filter-btn" [class.active]="nodeOpen()"
                (click)="nodeOpen.set(!nodeOpen())">
          Knoten&nbsp;<span class="filter-count">{{ nodeCountLabel() }}</span>
          <span class="chevron">{{ nodeOpen() ? '▲' : '▾' }}</span>
        </button>

        @if (nodeOpen()) {
          <div class="filter-panel" (click)="$event.stopPropagation()">
            <div class="panel-header">
              <span class="panel-title">Knotentypen</span>
              <div class="panel-actions">
                <button class="act-btn" (click)="setAllNodes(true)">Alle</button>
                <button class="act-btn" (click)="setAllNodes(false)">Keine</button>
                <button class="act-btn close-act" (click)="nodeOpen.set(false)">✕</button>
              </div>
            </div>
            <div class="panel-body">
              @for (group of nodeGroups; track group.label) {
                <div class="group">
                  <label class="group-header">
                    <input type="checkbox"
                           [checked]="isNodeGroupAllChecked(group)"
                           [indeterminate]="isNodeGroupPartial(group)"
                           (change)="toggleNodeGroup(group, $any($event.target).checked)" />
                    <strong>{{ group.label }}</strong>
                  </label>
                  <div class="group-items">
                    @for (k of group.kinds; track k) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isNodeChecked(k)"
                               (change)="toggleNode(k, $any($event.target).checked)" />
                        <span class="cb-label">{{ k }}</span>
                      </label>
                    }
                  </div>
                </div>
              }
            </div>
          </div>
        }
      </div>

      <!-- ── Reset ── -->
      @if (filter.nodeKindFilter.length || filter.edgeTypeFilter.length || filter.searchText) {
        <button class="reset-btn" (click)="filterReset.emit()">Zurücksetzen</button>
      }
    </div>
  `,
  styles: [`
    .toolbar {
      display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
      padding: .4rem .6rem; background: #f8fafc; border-bottom: 1px solid #e2e8f0;
    }
    .toolbar-group { display: flex; align-items: center; gap: .3rem; }
    .filter-group  { align-items: flex-start; }

    /* View-mode */
    .mode-btn { padding: 3px 10px; border: 1px solid #cbd5e1; background: #fff; border-radius: 4px; cursor: pointer; font-size: .8rem; }
    .mode-btn.active { background: #3b82f6; color: #fff; border-color: #3b82f6; }

    /* Search */
    .search-input { padding: 3px 8px; border: 1px solid #cbd5e1; border-radius: 4px; font-size: .85rem; width: 170px; }

    /* Layout */
    .filter-label  { font-size: .8rem; color: #555; white-space: nowrap; }
    .layout-select { font-size: .8rem; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 6px; background: #fff; }

    /* Filter button */
    .filter-btn {
      display: flex; align-items: center; gap: 4px;
      padding: 3px 10px; border: 1px solid #cbd5e1; background: #fff;
      border-radius: 4px; cursor: pointer; font-size: .8rem; white-space: nowrap;
    }
    .filter-btn:hover, .filter-btn.active { border-color: #3b82f6; background: #eff6ff; }
    .filter-count { font-size: .75rem; color: #3b82f6; font-weight: 600; }
    .chevron { font-size: .7rem; color: #888; }

    /* Dropdown panel */
    .filter-panel {
      position: absolute; top: calc(100% + 4px); left: 0; z-index: 200;
      width: 280px; max-height: 440px;
      background: #fff; border: 1px solid #e2e8f0; border-radius: 6px;
      box-shadow: 0 4px 16px rgba(0,0,0,.12);
      display: flex; flex-direction: column;
    }
    .panel-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 6px 10px; border-bottom: 1px solid #e2e8f0; flex-shrink: 0;
    }
    .panel-title { font-size: .8rem; font-weight: 700; color: #334; }
    .panel-actions { display: flex; gap: 4px; align-items: center; }
    .act-btn {
      padding: 2px 7px; font-size: .73rem; border: 1px solid #e2e8f0;
      border-radius: 3px; background: #f8fafc; cursor: pointer; color: #555;
    }
    .act-btn:hover { background: #e2e8f0; }
    .close-act { color: #888; }

    .panel-body { overflow-y: auto; padding: 6px 8px; display: flex; flex-direction: column; gap: 8px; }

    /* Groups */
    .group { display: flex; flex-direction: column; }
    .group-header {
      display: flex; align-items: center; gap: 6px;
      font-size: .78rem; color: #334; cursor: pointer; padding: 2px 0;
      user-select: none;
    }
    .group-header strong { font-size: .78rem; }
    .group-items {
      display: flex; flex-direction: column; gap: 1px;
      padding-left: 18px; margin-top: 2px;
    }
    .cb-row {
      display: flex; align-items: center; gap: 6px;
      font-size: .75rem; color: #444; cursor: pointer;
      padding: 1px 2px; border-radius: 2px; user-select: none;
    }
    .cb-row:hover { background: #f1f5f9; }
    .cb-label { font-family: ui-monospace, monospace; font-size: .72rem; }
    input[type=checkbox] { flex-shrink: 0; accent-color: #3b82f6; cursor: pointer; }

    /* Reset */
    .reset-btn { padding: 3px 8px; border: 1px solid #f87171; background: #fff; color: #b91c1c; border-radius: 4px; cursor: pointer; font-size: .8rem; }
  `],
})
export class GraphToolbarComponent implements OnChanges {
  @Input() activeMode: GraphViewMode = 'simple';
  @Input() layoutMode: GraphLayoutMode = 'tier';
  @Input() filter: GraphFilter = { searchText: '', nodeKindFilter: [], edgeTypeFilter: [] };
  @Input() webglAvailable = true;

  @Output() viewModeChange   = new EventEmitter<GraphViewMode>();
  @Output() layoutModeChange = new EventEmitter<GraphLayoutMode>();
  @Output() filterChange     = new EventEmitter<Partial<GraphFilter>>();
  @Output() filterReset      = new EventEmitter<void>();

  readonly viewModes       = GRAPH_VIEW_MODES;
  readonly modeLabels      = GRAPH_VIEW_MODE_LABELS;
  readonly layoutModes     = GRAPH_LAYOUT_MODES;
  readonly layoutModeLabels = GRAPH_LAYOUT_MODE_LABELS;
  readonly edgeGroups      = EDGE_GROUPS;
  readonly nodeGroups      = NODE_GROUPS;

  readonly edgeOpen = signal(false);
  readonly nodeOpen = signal(false);

  readonly edgeCountLabel = computed(() => {
    const f = this.filter.edgeTypeFilter;
    const shown = f.length === 0 ? ALL_EDGE_TYPES.length
                : f.includes('__none__' as GraphEdgeType) ? 0
                : f.length;
    return `${shown}/${ALL_EDGE_TYPES.length}`;
  });

  readonly nodeCountLabel = computed(() => {
    const f = this.filter.nodeKindFilter;
    const shown = f.length === 0 ? ALL_NODE_KINDS.length
                : f.includes('__none__' as GraphNodeKind) ? 0
                : f.length;
    return `${shown}/${ALL_NODE_KINDS.length}`;
  });

  ngOnChanges(): void {
    // panels stay open across filter changes — nothing to sync
  }

  // ── Edge helpers ─────────────────────────────────────────────────────────────

  isEdgeChecked(t: GraphEdgeType): boolean {
    const f = this.filter.edgeTypeFilter;
    if (f.length === 0) return true;
    if (f.includes('__none__' as GraphEdgeType)) return false;
    return f.includes(t);
  }

  isEdgeGroupAllChecked(g: EdgeGroup): boolean {
    return g.types.every(t => this.isEdgeChecked(t));
  }

  isEdgeGroupPartial(g: EdgeGroup): boolean {
    const checked = g.types.filter(t => this.isEdgeChecked(t)).length;
    return checked > 0 && checked < g.types.length;
  }

  toggleEdge(t: GraphEdgeType, checked: boolean): void {
    const base = this._edgeBase();
    const next = checked ? [...new Set([...base, t])] : base.filter(x => x !== t);
    this.filterChange.emit({ edgeTypeFilter: this._edgeNormalize(next) });
  }

  toggleEdgeGroup(g: EdgeGroup, checked: boolean): void {
    const base = this._edgeBase();
    const next = checked
      ? [...new Set([...base, ...g.types])]
      : base.filter(t => !g.types.includes(t));
    this.filterChange.emit({ edgeTypeFilter: this._edgeNormalize(next) });
  }

  setAllEdges(checked: boolean): void {
    this.filterChange.emit({ edgeTypeFilter: checked ? [] : ['__none__' as GraphEdgeType] });
  }

  // ── Node helpers ─────────────────────────────────────────────────────────────

  isNodeChecked(k: GraphNodeKind): boolean {
    const f = this.filter.nodeKindFilter;
    if (f.length === 0) return true;
    if (f.includes('__none__' as GraphNodeKind)) return false;
    return f.includes(k);
  }

  isNodeGroupAllChecked(g: NodeGroup): boolean {
    return g.kinds.every(k => this.isNodeChecked(k));
  }

  isNodeGroupPartial(g: NodeGroup): boolean {
    const checked = g.kinds.filter(k => this.isNodeChecked(k)).length;
    return checked > 0 && checked < g.kinds.length;
  }

  toggleNode(k: GraphNodeKind, checked: boolean): void {
    const base = this._nodeBase();
    const next = checked ? [...new Set([...base, k])] : base.filter(x => x !== k);
    this.filterChange.emit({ nodeKindFilter: this._nodeNormalize(next) });
  }

  toggleNodeGroup(g: NodeGroup, checked: boolean): void {
    const base = this._nodeBase();
    const next = checked
      ? [...new Set([...base, ...g.kinds])]
      : base.filter(k => !g.kinds.includes(k));
    this.filterChange.emit({ nodeKindFilter: this._nodeNormalize(next) });
  }

  setAllNodes(checked: boolean): void {
    this.filterChange.emit({ nodeKindFilter: checked ? [] : ['__none__' as GraphNodeKind] });
  }

  // ── Search ───────────────────────────────────────────────────────────────────

  onSearch(text: string): void {
    this.filterChange.emit({ searchText: text });
  }

  // ── Private helpers ──────────────────────────────────────────────────────────

  private _edgeBase(): GraphEdgeType[] {
    const f = this.filter.edgeTypeFilter;
    if (f.length === 0) return [...ALL_EDGE_TYPES];
    if (f.includes('__none__' as GraphEdgeType)) return [];
    return [...f];
  }

  private _edgeNormalize(types: GraphEdgeType[]): GraphEdgeType[] {
    if (types.length === 0) return ['__none__' as GraphEdgeType];
    if (types.length === ALL_EDGE_TYPES.length) return [];
    return types;
  }

  private _nodeBase(): GraphNodeKind[] {
    const f = this.filter.nodeKindFilter;
    if (f.length === 0) return [...ALL_NODE_KINDS];
    if (f.includes('__none__' as GraphNodeKind)) return [];
    return [...f];
  }

  private _nodeNormalize(kinds: GraphNodeKind[]): GraphNodeKind[] {
    if (kinds.length === 0) return ['__none__' as GraphNodeKind];
    if (kinds.length === ALL_NODE_KINDS.length) return [];
    return kinds;
  }
}
