import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterSummary } from '../model-training.models';
import { apiErrorMessage, idempotencyKey } from '../model-training-status';

@Component({
  selector: 'app-model-training-adapter-import',
  standalone: true,
  imports: [FormsModule, ExplanationNoticeComponent, FormFieldComponent, SectionCardComponent],
  template: `
    <app-section-card title="Bestehenden Adapter importieren" subtitle="Sicheres Bundle oder explizite PEFT-Dateikombination" variant="technical">
      <app-explanation-notice
        title="Import ist keine Freigabe"
        message="Der Hub prüft Manifest, Hash, Basismodell und safetensors. Ein erfolgreicher Import bleibt bis Evaluation und Approval inaktiv."
        tone="warning" />
      <div class="import-grid">
        <app-form-field label="Name" [required]="true"><input [(ngModel)]="name" maxlength="96" [disabled]="busy" /></app-form-field>
        <app-form-field label="Basismodell-ID" [required]="true"><input [(ngModel)]="baseModelId" [disabled]="busy" /></app-form-field>
        <app-form-field label="Methode"><select [(ngModel)]="method" [disabled]="busy"><option value="lora">LoRA</option><option value="qlora">QLoRA</option></select></app-form-field>
        <app-form-field label="Sicheres ZIP-Bundle" hint="Alternativ zur einzelnen Config-/Weight-Kombination.">
          <input type="file" accept="application/zip,.zip" (change)="selectBundle($event)" [disabled]="busy" />
        </app-form-field>
        <app-form-field label="adapter_config.json"><input type="file" accept="application/json,.json" (change)="selectConfig($event)" [disabled]="busy || !!bundle" /></app-form-field>
        <app-form-field label="adapter_model.safetensors"><input type="file" accept=".safetensors" (change)="selectWeights($event)" [disabled]="busy || !!bundle" /></app-form-field>
      </div>
      @if (selectionLabel()) { <p class="muted font-sm" role="status">{{ selectionLabel() }}</p> }
      @if (error) { <div class="state-banner error" role="alert">{{ error }}</div> }
      @if (busy) { <div role="status" aria-live="polite"><progress></progress> Adapter wird hochgeladen und sicher geprüft …</div> }
      <button type="button" (click)="importAdapter()" [disabled]="busy || !canImport()">Adapter importieren</button>
    </app-section-card>
  `,
  styles: [`
    .import-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin:12px 0; }
    progress { width:min(280px,100%); margin-right:8px; }
    @media (max-width:700px) { .import-grid { grid-template-columns:1fr; } }
  `],
})
export class AdapterImportComponent {
  private readonly facade = inject(ModelTrainingFacade);
  @Output() imported = new EventEmitter<AdapterSummary>();
  name = '';
  baseModelId = '';
  method: 'lora' | 'qlora' = 'lora';
  bundle: File | null = null;
  config: File | null = null;
  weights: File | null = null;
  busy = false;
  error = '';

  selectBundle(event: Event): void { this.bundle = this.fileFrom(event); if (this.bundle) { this.config = null; this.weights = null; } this.validateSelection(); }
  selectConfig(event: Event): void { this.config = this.fileFrom(event); this.validateSelection(); }
  selectWeights(event: Event): void { this.weights = this.fileFrom(event); this.validateSelection(); }

  canImport(): boolean {
    return Boolean(this.name.trim() && this.baseModelId.trim() && !this.error && (this.bundle || (this.config && this.weights)));
  }

  selectionLabel(): string {
    if (this.bundle) return `${this.bundle.name} · ${this.bundle.size} Bytes`;
    if (this.config || this.weights) return [this.config?.name, this.weights?.name].filter(Boolean).join(' + ');
    return '';
  }

  importAdapter(): void {
    if (!this.canImport() || this.busy) return;
    this.busy = true;
    this.error = '';
    this.facade.importAdapter({
      name: this.name,
      base_model_id: this.baseModelId,
      method: this.method,
      bundle: this.bundle,
      config: this.config,
      weights: this.weights,
    }, idempotencyKey('adapter-import')).pipe(finalize(() => this.busy = false)).subscribe({
      next: adapter => { this.imported.emit(adapter); this.name = ''; this.bundle = null; this.config = null; this.weights = null; },
      error: error => this.error = apiErrorMessage(error, 'Adapter konnte nicht importiert werden.'),
    });
  }

  private fileFrom(event: Event): File | null { return (event.target as HTMLInputElement | null)?.files?.[0] || null; }

  private validateSelection(): void {
    this.error = '';
    if (this.bundle && !/\.zip$/i.test(this.bundle.name)) this.error = 'Nur ZIP-Bundles sind erlaubt.';
    if (this.config && this.config.name !== 'adapter_config.json') this.error = 'Die Konfigurationsdatei muss adapter_config.json heißen.';
    if (this.weights && this.weights.name !== 'adapter_model.safetensors') this.error = 'Die Gewichtsdatei muss adapter_model.safetensors heißen.';
    const maxBytes = Number(this.facade.capabilities()?.limits?.max_adapter_bytes || 0);
    const totalBytes = Number(this.bundle?.size || 0) + Number(this.config?.size || 0) + Number(this.weights?.size || 0);
    if (maxBytes > 0 && totalBytes > maxBytes) this.error = 'Die Adapterdateien überschreiten das vom Hub gemeldete Limit.';
  }
}
