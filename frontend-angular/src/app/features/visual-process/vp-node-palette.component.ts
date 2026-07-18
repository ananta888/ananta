import { ChangeDetectionStrategy, Component, ElementRef, EventEmitter, Input, Output, QueryList, ViewChildren } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { CanvasHitTarget } from './vp-editor-context.models';
import { VpNodeDefinition } from './vp-node-definition-registry.service';

let paletteSequence = 0;

@Component({
  selector: 'app-vp-node-palette',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="vp-palette" aria-label="Node-Palette" (keydown)="onKeydown($event)">
      <div class="vp-palette-head">
        <label [for]="searchId">Node suchen</label>
        <button type="button" aria-label="Node-Palette schließen" (click)="closed.emit()">×</button>
      </div>
      <input [id]="searchId" class="vp-palette-search" type="search" [(ngModel)]="query"
             placeholder="Zweck, Label oder Capability" autocomplete="off" />
      <div class="vp-palette-results" role="list">
        @for (group of filteredGroups(); track group.category) {
          <section class="vp-palette-group" [attr.aria-label]="group.category">
            <h3>{{ group.category }}</h3>
            @for (definition of group.items; track definition.kind) {
              <button #paletteItem type="button" role="listitem" class="vp-palette-item"
                      [disabled]="!canAdd(definition)"
                      data-semantic-kind="palette_item" [attr.data-entity-id]="definition.kind"
                      [attr.aria-describedby]="purposeId(definition.kind)"
                      (pointerenter)="preview(definition)" (pointerleave)="previewed.emit(null)"
                      (focus)="preview(definition)" (click)="selected.emit(definition.kind)">
                <span class="vp-palette-label">{{ definition.label }}</span>
                <span class="vp-palette-kind">{{ definition.kind }}</span>
                <span [id]="purposeId(definition.kind)" class="vp-palette-purpose">{{ definition.purpose }}</span>
                <span class="vp-palette-contract">Inputs: {{ definition.inputs.length ? definition.inputs.join(', ') : 'keine deklariert' }}</span>
                <span class="vp-palette-contract">Outputs: {{ definition.outputs.length ? definition.outputs.join(', ') : 'keine deklariert' }}</span>
                <span class="vp-palette-flags">
                  {{ definition.capabilityFlags.executable ? definition.implementationStatus : 'nicht ausführbar' }}
                  @if (definition.capabilityFlags.requiresApproval) { · Freigabe }
                  @if (definition.sideEffects.length) { · Side Effects: {{ definition.sideEffects.join(', ') }} }
                </span>
              </button>
            }
          </section>
        } @empty {
          <p role="status">Keine passenden Nodes.</p>
        }
      </div>
    </aside>
  `,
  styles: [`
    :host{display:block;width:min(310px,45vw);min-width:240px;border-right:1px solid #263951;background:#0b1728;color:#dce8f8;overflow:auto}
    .vp-palette{padding:10px}.vp-palette-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.vp-palette-head label{font-weight:700}
    .vp-palette-head button{background:transparent;border:0;color:inherit;font-size:20px;cursor:pointer}.vp-palette-search{width:100%;margin:8px 0;padding:7px;border:1px solid #35506f;border-radius:5px;background:#07111f;color:inherit}
    h3{margin:10px 0 5px;font-size:11px;text-transform:uppercase;opacity:.7}.vp-palette-item{display:flex;width:100%;flex-direction:column;align-items:flex-start;gap:2px;padding:8px;margin:3px 0;border:1px solid #263951;border-radius:6px;background:#101f33;color:inherit;text-align:left;cursor:pointer}
    .vp-palette-item:hover,.vp-palette-item:focus-visible{border-color:#68b5ff;outline:2px solid transparent;background:#162b45}.vp-palette-item:disabled{opacity:.6;cursor:not-allowed}.vp-palette-label{font-weight:700}.vp-palette-kind{font:10px monospace;opacity:.7}.vp-palette-purpose,.vp-palette-contract,.vp-palette-flags{font-size:11px;opacity:.82}.vp-palette-contract{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.vp-palette-flags{color:#ffd29c}
  `],
})
export class VpNodePaletteComponent {
  readonly instanceId = `vp-node-palette-${++paletteSequence}`;
  readonly searchId = `${this.instanceId}-search`;
  @ViewChildren('paletteItem', { read: ElementRef }) paletteItems!: QueryList<ElementRef<HTMLButtonElement>>;
  @Input({ required: true }) definitions: readonly VpNodeDefinition[] = [];
  @Input({ required: true }) graphId = '';
  @Output() selected = new EventEmitter<string>();
  @Output() previewed = new EventEmitter<CanvasHitTarget | null>();
  @Output() closed = new EventEmitter<void>();
  query = '';

  filteredGroups(): Array<{ category: string; items: VpNodeDefinition[] }> {
    const needle = this.query.trim().toLocaleLowerCase();
    const filtered = this.definitions.filter(definition => !needle || [
      definition.kind,
      definition.label,
      definition.category,
      definition.purpose,
      definition.implementationState,
      definition.implementationStatus,
      definition.riskLevel,
      ...definition.sideEffects,
      ...Object.entries(definition.capabilityFlags).filter(([, enabled]) => enabled).map(([name]) => name),
      ...definition.keywords,
    ].join(' ').toLocaleLowerCase().includes(needle));
    const groups = new Map<string, VpNodeDefinition[]>();
    for (const definition of filtered) {
      groups.set(definition.category, [...(groups.get(definition.category) ?? []), definition]);
    }
    return Array.from(groups.entries())
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([category, items]) => ({ category, items }));
  }

  preview(definition: VpNodeDefinition): void {
    this.previewed.emit({
      kind: 'palette_item',
      entityId: definition.kind,
      graphId: this.graphId,
      role: 'node-template',
    });
  }

  canAdd(definition: VpNodeDefinition): boolean {
    return definition.supported && definition.capabilityFlags.executable;
  }

  purposeId(kind: string): string {
    return `${this.instanceId}-purpose-${kind.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  }

  onKeydown(event: KeyboardEvent): void {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const items = (this.paletteItems?.toArray() ?? [])
      .map(item => item.nativeElement)
      .filter(item => !item.disabled);
    if (!items.length) return;
    const current = items.indexOf(event.target as HTMLButtonElement);
    const nextIndex = event.key === 'Home' ? 0
      : event.key === 'End' ? items.length - 1
        : event.key === 'ArrowUp' ? (current <= 0 ? items.length - 1 : current - 1)
          : (current + 1) % items.length;
    event.preventDefault();
    items[nextIndex].focus();
  }
}
