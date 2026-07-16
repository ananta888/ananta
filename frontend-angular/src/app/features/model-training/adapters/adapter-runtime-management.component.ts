import { Component, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, finalize } from 'rxjs';

import { ExplanationNoticeComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { ModelTrainingFacade } from '../model-training.facade';
import { AdapterRuntimeRollbackResult, AdapterRuntimeUnloadResult } from '../model-training.models';
import { apiErrorMessage, boundedText } from '../model-training-status';

type RuntimeAction = 'unload' | 'rollback';

@Component({
  selector: 'app-model-training-adapter-runtime-management',
  standalone: true,
  imports: [ExplanationNoticeComponent, FormsModule, FormFieldComponent, SectionCardComponent],
  template: `
    @if (facade.selectedAdapter(); as adapter) {
      <app-section-card
        title="Inferenz-Runtime verwalten"
        [subtitle]="adapter.name + ' · Admin-Kommandos über den Hub'"
        variant="primary">
        <app-explanation-notice
          title="Getrennt vom Registry-Lifecycle"
          message="Cache-Unload entfernt nur geladene Runtime-Gewichte. Runtime-Rollback deprecated den gewählten Adapter, entlädt ihn und wechselt ausschließlich auf einen genehmigten Vorgänger oder das Basismodell."
          tone="technical" />
        <div class="runtime-controls">
          <app-form-field label="Runtime-Aktion" [required]="true">
            <select [(ngModel)]="action" [disabled]="busy()">
              <option value="unload">Nur Runtime-Cache entladen</option>
              <option value="rollback">Runtime-Rollback mit sicherem Fallback</option>
            </select>
          </app-form-field>
          <app-form-field label="Operative Begründung" [required]="true" [hint]="reasonHint()">
            <textarea [(ngModel)]="reason" rows="3" minlength="10" maxlength="512" [disabled]="busy()"></textarea>
          </app-form-field>
        </div>
        <label class="confirmation">
          <input type="checkbox" [(ngModel)]="confirmed" [disabled]="busy()" />
          Ich bestätige dieses Admin-Runtime-Kommando und seine Auswirkungen auf Cache bzw. sicheren Fallback.
        </label>
        <button type="button" (click)="execute()" [disabled]="!canExecute()">
          {{ busy() ? 'Runtime-Kommando läuft …' : actionLabel() }}
        </button>
        @if (resultMessage()) { <div class="state-banner success" role="status">{{ resultMessage() }}</div> }
        @if (error()) { <div class="state-banner error" role="alert">{{ error() }}</div> }
      </app-section-card>
    }
  `,
  styles: [`
    .runtime-controls { display:grid; grid-template-columns:minmax(220px,.8fr) minmax(0,1.8fr); gap:12px; align-items:start; }
    .confirmation { display:flex; gap:8px; align-items:flex-start; margin:12px 0; }
    @media (max-width:700px) { .runtime-controls { grid-template-columns:1fr; } }
  `],
})
export class AdapterRuntimeManagementComponent {
  readonly facade = inject(ModelTrainingFacade);
  action: RuntimeAction = 'unload';
  reason = '';
  confirmed = false;
  readonly busy = signal(false);
  readonly error = signal('');
  readonly resultMessage = signal('');
  private activeAdapterId = '';

  constructor() {
    effect(() => {
      const adapterId = this.facade.selectedAdapter()?.id || '';
      if (adapterId === this.activeAdapterId) return;
      this.activeAdapterId = adapterId;
      this.reason = '';
      this.confirmed = false;
      this.error.set('');
      this.resultMessage.set('');
    });
  }

  reasonHint(): string {
    const length = this.reason.trim().length;
    return `${length}/512 Zeichen; der Hub verlangt mindestens 10 aussagekräftige Zeichen.`;
  }

  canExecute(): boolean {
    const length = this.reason.trim().length;
    return Boolean(this.facade.selectedAdapter() && this.confirmed && !this.busy() && length >= 10 && length <= 512);
  }

  actionLabel(): string {
    return this.action === 'unload' ? 'Runtime-Cache jetzt entladen' : 'Runtime-Rollback jetzt ausführen';
  }

  execute(): void {
    const adapter = this.facade.selectedAdapter();
    if (!adapter || !this.canExecute()) return;
    const payload = { confirmed: true as const, reason: this.reason.trim() };
    this.busy.set(true);
    this.error.set('');
    this.resultMessage.set('');
    const operation: Observable<AdapterRuntimeUnloadResult | AdapterRuntimeRollbackResult> = this.action === 'unload'
      ? this.facade.unloadRuntimeAdapter(adapter.id, payload)
      : this.facade.rollbackRuntimeAdapter(adapter.id, {
          ...payload,
          expected_version: adapter.registry_version ?? adapter.version,
        });
    operation.pipe(finalize(() => this.busy.set(false))).subscribe({
      next: result => {
        this.confirmed = false;
        this.resultMessage.set(this.describeResult(result));
      },
      error: error => this.error.set(apiErrorMessage(error, 'Runtime-Kommando ist fehlgeschlagen.')),
    });
  }

  private describeResult(result: AdapterRuntimeUnloadResult | AdapterRuntimeRollbackResult): string {
    if ('rollback_target' in result) {
      const target = result.rollback_target.type === 'adapter'
        ? `genehmigter Adapter ${result.rollback_target.adapter_id} v${result.rollback_target.version}`
        : `Basismodell ${result.rollback_target.base_model}`;
      return boundedText(`Runtime-Rollback bestätigt; sicherer Zielpfad: ${target}. Cache: ${result.cache_unload.reason_code || result.cache_unload.status}.`, 700);
    }
    return boundedText(`Runtime-Cache-Kommando: ${result.reason_code || result.status}. Registry-Freigabe wurde nicht verändert.`, 700);
  }
}
