import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { VpAssistantPatchPreview } from './vp-assistant-api.service';
import type { VpAssistantPatchUiStatus } from './vp-assistant-bridge.service';
import { canonicalVpJson } from './vp-assistant-context.service';
import { VpHelpEvidence, VpWorkflowPatch } from './vp-editor-context.models';
import { ValidationResult, VpGraph } from './visual-process-api.service';

let patchPreviewSequence = 0;

@Component({
  selector: 'app-vp-workflow-patch-preview',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown.escape)': 'closed.emit()' },
  template: `
    <section class="patch-dialog" role="dialog" aria-modal="true" [attr.aria-labelledby]="titleId">
      <header><h3 [id]="titleId">AI-Patch-Vorschau</h3><button type="button" aria-label="Vorschau schließen" (click)="closed.emit()">×</button></header>
      <p>Der Hub hat {{ preview.operation_count }} Operation(en) atomar gegen einen Graphklon geprüft. Noch wurde nichts am Editor-Draft geändert.</p>
      <div class="hashes"><code>{{ preview.base_graph_hash }}</code> → <code>{{ preview.preview_graph_hash }}</code></div>

      <h4>Operationen</h4>
      <ol>
        @for (operation of patch.operations; track operation.operation_id) {
          <li><strong>{{ operation.op }}</strong> · {{ operation.step_id || operation.edge_id || operation.temp_id || operation.path || 'Graph' }}
            @if (operation.path) {
              <code>{{ operation.path }}</code>
              <span> · {{ formatPatchValue(operation.expected_old_value) }} → {{ formatPatchValue(operation.value) }}</span>
            }
          </li>
        }
      </ol>

      <h4>Hub-Policy</h4>
      <p data-testid="vp-patch-policy">Entscheidung: <strong>{{ preview.decision }}</strong> · Audit: <code>{{ preview.audit_id }}</code></p>

      <h4>Betroffene Definitionen</h4>
      <ul>
        @for (change of graphChanges(); track change) { <li>{{ change }}</li> }
        @if (!graphChanges().length) { <li>Keine strukturelle Differenz.</li> }
      </ul>

      <div class="validation-grid">
        <div><strong>Vorher</strong><span>{{ currentValidation?.valid === true ? 'gültig' : currentValidation?.valid === false ? 'ungültig' : 'nicht validiert' }}</span>
          @if (currentValidation) { <small>{{ currentValidation.error_count }} Fehler · {{ currentValidation.warning_count }} Warnungen</small> }
        </div>
        <div><strong>Nachher (Hub)</strong><span>{{ preview.validation.valid ? 'gültig' : 'ungültig' }}</span>
          <small>{{ preview.validation.error_count }} Fehler · {{ preview.validation.warning_count }} Warnungen</small>
        </div>
      </div>
      @if (preview.validation.issues.length) {
        <ul class="warnings">@for (issue of preview.validation.issues; track $index) { <li>{{ issue.code }} · {{ issue.path || '/' }} · {{ issue.message }}</li> }</ul>
      }

      @if (evidence.length) {
        <h4>Freigegebene Belege</h4><ul>
          @for (item of evidence; track item.evidence_id) {
            <li><code>{{ item.source_id || item.evidence_id }}</code> · {{ item.verification_status }}@if (item.path) { · {{ item.path }} }</li>
          }
        </ul>
      }

      @if (errorCode) { <div class="error" role="alert">{{ status }} · <code>{{ errorCode }}</code></div> }
      @if (status === 'conflict') {
        <div class="conflict-action" role="status">
          <span>Der Draft hat sich geändert. Der bisherige Patch bleibt gesperrt.</span>
          <button type="button" data-testid="vp-patch-refresh" (click)="refreshRequested.emit()" [disabled]="readOnly">
            Neuen Hub-Patch für aktuellen Stand erzeugen
          </button>
        </div>
      }
      @if (status === 'loading') { <div role="status">Der Hub validiert den aktuellen Draft und erzeugt einen neuen Patch …</div> }
      @if (status === 'applied') { <div class="success" role="status">Patch als eine lokale Editor-Transaktion übernommen. Speichern bleibt ein separater Schritt.</div> }
      @if (status === 'rejected') { <div role="status">Patch verworfen; der Draft blieb unverändert.</div> }

      <footer>
        <label [for]="confirmationId"><input [id]="confirmationId" type="checkbox" [(ngModel)]="confirmed" [disabled]="!canApply()" />
          Vollständigen Patch atomar in den lokalen Draft übernehmen</label>
        <div>
          <button type="button" (click)="rejected.emit()" [disabled]="status !== 'ready'">Verwerfen</button>
          <button type="button" class="primary" (click)="accepted.emit()" [disabled]="!confirmed || !canApply()">Bestätigt übernehmen</button>
        </div>
      </footer>
    </section>
  `,
  styles: [`
    :host{position:absolute;inset:0;z-index:40;display:grid;place-items:center;background:#020711a8;padding:18px}.patch-dialog{width:min(700px,calc(100vw - 28px));max-height:min(82vh,760px);overflow:auto;background:#111a27;color:#eef5ff;border:1px solid #68b5ff;border-radius:12px;padding:14px;box-shadow:0 16px 48px #000a}header{display:flex;align-items:center;justify-content:space-between;position:sticky;top:-14px;background:#111a27;z-index:1}h3,h4{margin:6px 0}button{border:1px solid #496681;border-radius:5px;background:#172942;color:inherit;padding:6px 9px}.primary{background:#1c6655;border-color:#62c5a9}.hashes{display:flex;gap:6px;align-items:center;overflow-wrap:anywhere}.hashes code{font-size:9px}.validation-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.validation-grid>div{display:flex;flex-direction:column;border:1px solid #385271;border-radius:6px;padding:8px}.warnings,.error{color:#ffd0bd}.error,.success,.conflict-action{padding:7px;border:1px solid currentColor;border-radius:6px;margin:8px 0}.success{color:#aef4d8}.conflict-action{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#ffd88a}footer{display:grid;gap:10px;margin-top:12px;border-top:1px solid #385271;padding-top:10px}footer div{display:flex;justify-content:flex-end;gap:8px}button:focus-visible,input:focus-visible{outline:2px solid #ffd166;outline-offset:2px}@media(max-width:600px){:host{padding:7px}.validation-grid{grid-template-columns:1fr}.conflict-action{align-items:stretch;flex-direction:column}}
  `],
})
export class VpWorkflowPatchPreviewComponent {
  readonly confirmationId = `vp-patch-confirm-${++patchPreviewSequence}`;
  readonly titleId = `vp-patch-title-${patchPreviewSequence}`;
  @Input({ required: true }) preview!: VpAssistantPatchPreview;
  @Input({ required: true }) patch!: VpWorkflowPatch;
  @Input({ required: true }) originalGraph!: VpGraph;
  @Input() currentValidation: ValidationResult | null = null;
  @Input() evidence: readonly VpHelpEvidence[] = [];
  @Input() status: VpAssistantPatchUiStatus = 'ready';
  @Input() errorCode: string | null = null;
  @Input() readOnly = false;
  @Output() accepted = new EventEmitter<void>();
  @Output() rejected = new EventEmitter<void>();
  @Output() refreshRequested = new EventEmitter<void>();
  @Output() closed = new EventEmitter<void>();
  confirmed = false;

  graphChanges(): string[] {
    if (!this.originalGraph || !this.preview?.preview_graph) return [];
    return [
      ...this.entityChanges('Node', this.originalGraph.steps, this.preview.preview_graph.steps),
      ...this.entityChanges('Kante', this.originalGraph.edges, this.preview.preview_graph.edges),
    ];
  }

  canApply(): boolean {
    const decision = String(this.preview?.decision ?? '').toLocaleLowerCase('en-US');
    return !this.readOnly && this.status === 'ready' && this.preview?.validation?.valid === true
      && !['rejected', 'denied', 'blocked'].includes(decision);
  }

  formatPatchValue(value: unknown): string {
    if (value === undefined) return 'nicht gesetzt';
    const safe = this.redact(value);
    if (typeof safe === 'string') return safe;
    try { return JSON.stringify(safe); } catch { return String(safe); }
  }

  private entityChanges(label: string, before: Array<{ id: string }>, after: Array<{ id: string }>): string[] {
    const previous = new Map(before.map(item => [item.id, item]));
    const next = new Map(after.map(item => [item.id, item]));
    return [
      ...after.filter(item => !previous.has(item.id)).map(item => `${label} hinzugefügt: ${item.id}`),
      ...before.filter(item => !next.has(item.id)).map(item => `${label} entfernt: ${item.id}`),
      ...after.filter(item => previous.has(item.id) && canonicalVpJson(previous.get(item.id)) !== canonicalVpJson(item))
        .map(item => `${label} geändert: ${item.id}`),
    ];
  }

  private redact(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(item => this.redact(item));
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, item]) => {
      const normalized = key.toLocaleLowerCase('en-US').replaceAll('-', '_');
      const secret = ['api_key', 'apikey', 'access_token', 'refresh_token', 'password', 'client_secret', 'private_key'].includes(normalized)
        && !normalized.endsWith('_secret_ref');
      return [key, secret ? '[REDACTED]' : this.redact(item)];
    }));
  }
}
