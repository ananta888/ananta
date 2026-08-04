import {
  Component, Input, Output, EventEmitter, ChangeDetectionStrategy, signal,
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { GraphFilter, GraphFilterSelection, EMPTY_FILTER, graphSelectionContains, graphSelectionFromVisible } from '../../models/graph-filter.model';
import { GraphViewMode, GRAPH_VIEW_MODES, GRAPH_VIEW_MODE_LABELS } from '../../models/graph-view-mode';
import { GraphLayoutMode, GRAPH_LAYOUT_MODES, GRAPH_LAYOUT_MODE_LABELS } from '../../models/graph-layout-mode';
import { ALL_NODE_KINDS, ALL_EDGE_TYPES } from '../../models/graph-filter.model';
import {
  GRAPH_NEIGHBORHOOD_DEPTH_OPTIONS,
  MAX_GRAPH_NEIGHBORHOOD_DEPTH,
} from '../../models/graph-neighborhood.model';

// ── Edge-type groups ──────────────────────────────────────────────────────────

interface EdgeGroup { label: string; types: string[]; }
interface NodeGroup { label: string; kinds: string[]; }

export interface GraphDomainFilterOption {
  readonly domainId: string;
  readonly label: string;
  readonly nodeCount: number;
}

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
  imports: [FormsModule],
  template: `
    <div class="toolbar">

      <!-- ── View-mode buttons ── -->
      <div class="toolbar-group">
        @for (mode of viewModes; track mode) {
          @if (mode !== '3d' || webglAvailable) {
            <button class="mode-btn" [class.active]="activeMode === mode"
                    [attr.aria-label]="modeLabels[mode] + ' Graphansicht'"
                    [attr.aria-pressed]="activeMode === mode"
                    [attr.data-testid]="'graph-view-mode-' + mode"
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
                @if (edgeTypesForGroup(group).length) {
                <div class="group">
                  <label class="group-header">
                    <input type="checkbox"
                           [checked]="isEdgeGroupAllChecked(group)"
                           [indeterminate]="isEdgeGroupPartial(group)"
                           (change)="toggleEdgeGroup(group, $any($event.target).checked)" />
                    <strong>{{ group.label }}</strong>
                  </label>
                  <div class="group-items">
                    @for (t of edgeTypesForGroup(group); track t) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isEdgeChecked(t)"
                               (change)="toggleEdge(t, $any($event.target).checked)" />
                        <span class="cb-label">{{ t }}</span>
                      </label>
                    }
                  </div>
                </div>
                }
              }
              @if (otherEdgeTypes().length) {
                <div class="group">
                  <strong class="group-header">Dynamisch / unbekannt</strong>
                  <div class="group-items">
                    @for (type of otherEdgeTypes(); track type) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isEdgeChecked(type)"
                               (change)="toggleEdge(type, $any($event.target).checked)" />
                        <span class="cb-label">{{ type }}</span>
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
                @if (nodeKindsForGroup(group).length) {
                <div class="group">
                  <label class="group-header">
                    <input type="checkbox"
                           [checked]="isNodeGroupAllChecked(group)"
                           [indeterminate]="isNodeGroupPartial(group)"
                           (change)="toggleNodeGroup(group, $any($event.target).checked)" />
                    <strong>{{ group.label }}</strong>
                  </label>
                  <div class="group-items">
                    @for (k of nodeKindsForGroup(group); track k) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isNodeChecked(k)"
                               (change)="toggleNode(k, $any($event.target).checked)" />
                        <span class="cb-label">{{ k }}</span>
                      </label>
                    }
                  </div>
                </div>
                }
              }
              @if (otherNodeKinds().length) {
                <div class="group">
                  <strong class="group-header">Dynamisch / unbekannt</strong>
                  <div class="group-items">
                    @for (kind of otherNodeKinds(); track kind) {
                      <label class="cb-row">
                        <input type="checkbox" [checked]="isNodeChecked(kind)"
                               (change)="toggleNode(kind, $any($event.target).checked)" />
                        <span class="cb-label">{{ kind }}</span>
                      </label>
                    }
                  </div>
                </div>
              }
            </div>
          </div>
        }
      </div>

      <!-- ── Domain filter, independent from optional visual legends ── -->
      <div class="toolbar-group filter-group" style="position:relative">
        <button class="filter-btn" [class.active]="domainOpen()"
                data-testid="graph-domain-filter"
                [attr.aria-expanded]="domainOpen()"
                [attr.aria-controls]="domainPanelId"
                (click)="domainOpen.set(!domainOpen())">
          Domains&nbsp;<span class="filter-count">{{ domainCountLabel() }}</span>
          <span class="chevron">{{ domainOpen() ? '▲' : '▾' }}</span>
        </button>

        @if (domainOpen()) {
          <div class="filter-panel domain-panel" [id]="domainPanelId" role="group"
               [attr.aria-labelledby]="domainPanelTitleId"
               (click)="$event.stopPropagation()">
            <div class="panel-header">
              <span class="panel-title" [id]="domainPanelTitleId">Domains im Ausschnitt</span>
              <div class="panel-actions">
                <button class="act-btn" (click)="setAllDomains(true)">Alle</button>
                <button class="act-btn" (click)="setAllDomains(false)">Keine</button>
                <button class="act-btn close-act" aria-label="Domainfilter schließen"
                        (click)="domainOpen.set(false)">✕</button>
              </div>
            </div>
            <div class="panel-body">
              @for (domain of domainOptions; track domain.domainId) {
                <label class="cb-row domain-row">
                  <input type="checkbox" [checked]="isDomainChecked(domain.domainId)"
                         (change)="toggleDomain(domain.domainId, $any($event.target).checked)" />
                  <span class="domain-label">{{ domain.label }}</span>
                  <span class="domain-count">{{ domain.nodeCount }}</span>
                </label>
              } @empty {
                <p class="panel-empty">Keine Domaininformationen im geladenen Ausschnitt.</p>
              }
            </div>
          </div>
        }
      </div>

      <!-- ── Loaded-window neighbourhood depth ── -->
      <div class="toolbar-group neighbourhood-group"
           title="Eine Verknüpfungsstufe entspricht genau einer sichtbaren Kante. Die Kantenrichtung wird für die Nachbarschaft ignoriert.">
        <span class="filter-label">Verknüpfungstiefe:</span>
        <select class="depth-select" aria-label="Verknüpfungstiefe im geladenen Graph-Ausschnitt"
                data-testid="graph-neighbourhood-depth"
                [ngModel]="neighborhoodDepth"
                (ngModelChange)="changeNeighborhoodDepth($event)">
          <option [ngValue]="0">Alle geladenen</option>
          @for (depth of neighborhoodDepths; track depth) {
            <option [ngValue]="depth">{{ depth }} {{ depth === 1 ? 'Kante' : 'Kanten' }}</option>
          }
        </select>
        @if (neighborhoodDepth > 0) {
          <span class="depth-status" data-testid="graph-neighbourhood-status">
            {{ neighborhoodAnchorLabel || 'Knoten wählen' }}
          </span>
        }
      </div>

      <!-- ── Reset ── -->
      @if (hasFilters()) {
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
    .depth-select { font-size: .8rem; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 6px; background: #fff; }
    .neighbourhood-group { max-width: 380px; }
    .depth-status { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .72rem; color: #7c3aed; }

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
    .domain-panel { width: 320px; }
    .domain-row { padding: 3px 2px; }
    .domain-label { min-width: 0; flex: 1; overflow-wrap: anywhere; }
    .domain-count { color: #64748b; font-variant-numeric: tabular-nums; }
    .panel-empty { margin: .35rem; font-size: .75rem; color: #64748b; }
    input[type=checkbox] { flex-shrink: 0; accent-color: #3b82f6; cursor: pointer; }

    /* Reset */
    .reset-btn { padding: 3px 8px; border: 1px solid #f87171; background: #fff; color: #b91c1c; border-radius: 4px; cursor: pointer; font-size: .8rem; }
  `],
})
export class GraphToolbarComponent {
  private static instanceSequence = 0;

  @Input() activeMode: GraphViewMode = 'simple';
  @Input() layoutMode: GraphLayoutMode = 'tier';
  @Input() filter: GraphFilter = EMPTY_FILTER;
  /** Inventory comes from the unfiltered canonical graph and includes unknown raw values. */
  @Input() nodeKinds: readonly string[] | null = null;
  @Input() edgeTypes: readonly string[] | null = null;
  @Input() domainOptions: readonly GraphDomainFilterOption[] = [];
  @Input() neighborhoodDepth = 0;
  @Input() neighborhoodAnchorLabel = '';
  @Input() webglAvailable = true;

  @Output() viewModeChange   = new EventEmitter<GraphViewMode>();
  @Output() layoutModeChange = new EventEmitter<GraphLayoutMode>();
  @Output() filterChange     = new EventEmitter<Partial<GraphFilter>>();
  @Output() filterReset      = new EventEmitter<void>();
  @Output() neighborhoodDepthChange = new EventEmitter<number>();

  readonly viewModes       = GRAPH_VIEW_MODES;
  readonly modeLabels      = GRAPH_VIEW_MODE_LABELS;
  readonly layoutModes     = GRAPH_LAYOUT_MODES;
  readonly layoutModeLabels = GRAPH_LAYOUT_MODE_LABELS;
  readonly edgeGroups      = EDGE_GROUPS;
  readonly nodeGroups      = NODE_GROUPS;
  readonly neighborhoodDepths = GRAPH_NEIGHBORHOOD_DEPTH_OPTIONS;
  readonly domainPanelId = `graph-domain-filter-panel-${++GraphToolbarComponent.instanceSequence}`;
  readonly domainPanelTitleId = `${this.domainPanelId}-title`;

  readonly edgeOpen = signal(false);
  readonly nodeOpen = signal(false);
  readonly domainOpen = signal(false);

  edgeCountLabel(): string {
    const inventory = this._edgeInventory();
    return `${this._visibleCount(this.filter.edgeTypes, inventory)}/${inventory.length}`;
  }

  nodeCountLabel(): string {
    const inventory = this._nodeInventory();
    return `${this._visibleCount(this.filter.nodeKinds, inventory)}/${inventory.length}`;
  }

  domainCountLabel(): string {
    return `${this._visibleCount(this.filter.domains, this._domainInventory())}/${this.domainOptions.length}`;
  }

  // panels stay open across filter changes — no ngOnChanges sync needed

  // ── Edge helpers ─────────────────────────────────────────────────────────────

  isEdgeChecked(type: string): boolean {
    return graphSelectionContains(this.filter.edgeTypes, type);
  }

  isEdgeGroupAllChecked(g: EdgeGroup): boolean {
    return this.edgeTypesForGroup(g).every(type => this.isEdgeChecked(type));
  }

  isEdgeGroupPartial(g: EdgeGroup): boolean {
    const types = this.edgeTypesForGroup(g);
    const checked = types.filter(type => this.isEdgeChecked(type)).length;
    return checked > 0 && checked < types.length;
  }

  toggleEdge(t: string, checked: boolean): void {
    const base = this._edgeBase();
    const next = checked ? [...new Set([...base, t])] : base.filter(x => x !== t);
    this.filterChange.emit({ edgeTypes: graphSelectionFromVisible(next, this._edgeInventory()) });
  }

  toggleEdgeGroup(g: EdgeGroup, checked: boolean): void {
    const base = this._edgeBase();
    const next = checked
      ? [...new Set([...base, ...this.edgeTypesForGroup(g)])]
      : base.filter(type => !this.edgeTypesForGroup(g).includes(type));
    this.filterChange.emit({ edgeTypes: graphSelectionFromVisible(next, this._edgeInventory()) });
  }

  setAllEdges(checked: boolean): void {
    this.filterChange.emit({ edgeTypes: checked ? { mode: 'all', values: [] } : { mode: 'none', values: [] } });
  }

  // ── Node helpers ─────────────────────────────────────────────────────────────

  isNodeChecked(kind: string): boolean {
    return graphSelectionContains(this.filter.nodeKinds, kind);
  }

  isNodeGroupAllChecked(g: NodeGroup): boolean {
    return this.nodeKindsForGroup(g).every(kind => this.isNodeChecked(kind));
  }

  isNodeGroupPartial(g: NodeGroup): boolean {
    const kinds = this.nodeKindsForGroup(g);
    const checked = kinds.filter(kind => this.isNodeChecked(kind)).length;
    return checked > 0 && checked < kinds.length;
  }

  toggleNode(k: string, checked: boolean): void {
    const base = this._nodeBase();
    const next = checked ? [...new Set([...base, k])] : base.filter(x => x !== k);
    this.filterChange.emit({ nodeKinds: graphSelectionFromVisible(next, this._nodeInventory()) });
  }

  toggleNodeGroup(g: NodeGroup, checked: boolean): void {
    const base = this._nodeBase();
    const next = checked
      ? [...new Set([...base, ...this.nodeKindsForGroup(g)])]
      : base.filter(kind => !this.nodeKindsForGroup(g).includes(kind));
    this.filterChange.emit({ nodeKinds: graphSelectionFromVisible(next, this._nodeInventory()) });
  }

  setAllNodes(checked: boolean): void {
    this.filterChange.emit({ nodeKinds: checked ? { mode: 'all', values: [] } : { mode: 'none', values: [] } });
  }

  // ── Domain helpers ───────────────────────────────────────────────────────────

  isDomainChecked(domainId: string): boolean {
    return graphSelectionContains(this.filter.domains, domainId);
  }

  toggleDomain(domainId: string, checked: boolean): void {
    const inventory = this._domainInventory();
    const filter = this.filter.domains;
    const base = filter.mode === 'all'
      ? [...inventory]
      : filter.mode === 'none' ? [] : [...filter.values];
    const next = checked
      ? [...new Set([...base, domainId])]
      : base.filter(value => value !== domainId);
    this.filterChange.emit({ domains: graphSelectionFromVisible(next, inventory) });
  }

  setAllDomains(checked: boolean): void {
    this.filterChange.emit({
      domains: checked ? { mode: 'all', values: [] } : { mode: 'none', values: [] },
    });
  }

  changeNeighborhoodDepth(value: number | string): void {
    const parsed = Number(value);
    const depth = Number.isFinite(parsed)
      ? Math.max(0, Math.min(MAX_GRAPH_NEIGHBORHOOD_DEPTH, Math.floor(parsed)))
      : 0;
    this.neighborhoodDepthChange.emit(depth);
  }

  // ── Search ───────────────────────────────────────────────────────────────────

  onSearch(text: string): void {
    this.filterChange.emit({ searchText: text });
  }

  // ── Private helpers ──────────────────────────────────────────────────────────

  private _edgeBase(): string[] {
    const filter = this.filter.edgeTypes;
    if (filter.mode === 'all') return [...this._edgeInventory()];
    if (filter.mode === 'none') return [];
    return [...filter.values];
  }

  private _nodeBase(): string[] {
    const filter = this.filter.nodeKinds;
    if (filter.mode === 'all') return [...this._nodeInventory()];
    if (filter.mode === 'none') return [];
    return [...filter.values];
  }

  hasFilters(): boolean {
    return !!this.filter.searchText ||
      this.filter.nodeKinds.mode !== 'all' ||
      this.filter.edgeTypes.mode !== 'all' ||
      this.filter.domains.mode !== 'all';
  }

  edgeTypesForGroup(group: EdgeGroup): string[] {
    const inventory = this._edgeInventory();
    return group.types.filter(type => inventory.includes(type));
  }

  nodeKindsForGroup(group: NodeGroup): string[] {
    const inventory = this._nodeInventory();
    return group.kinds.filter(kind => inventory.includes(kind));
  }

  otherEdgeTypes(): string[] {
    const registered = new Set(this.edgeGroups.flatMap(group => group.types));
    return this._edgeInventory().filter(type => !registered.has(type));
  }

  otherNodeKinds(): string[] {
    const registered = new Set(this.nodeGroups.flatMap(group => group.kinds));
    return this._nodeInventory().filter(kind => !registered.has(kind));
  }

  private _edgeInventory(): readonly string[] {
    return this.edgeTypes ?? ALL_EDGE_TYPES;
  }

  private _nodeInventory(): readonly string[] {
    return this.nodeKinds ?? ALL_NODE_KINDS;
  }

  private _domainInventory(): readonly string[] {
    return this.domainOptions.map(option => option.domainId);
  }

  private _visibleCount(selection: GraphFilterSelection<string>, inventory: readonly string[]): number {
    if (selection.mode === 'all') return inventory.length;
    if (selection.mode === 'none') return 0;
    return selection.values.filter(value => inventory.includes(value)).length;
  }
}
