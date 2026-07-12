import { ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnChanges, inject } from '@angular/core';

import { VoiceApiService } from './voice-api.service';
import { VoiceCapabilityStatus, VoiceModelCapability } from './voice.models';
import { voiceError } from './voice-ui.helpers';

@Component({
  selector: 'app-voice-runtime-status',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-runtime-status.component.html',
  styleUrl: './voice-settings.css',
})
export class VoiceRuntimeStatusComponent implements OnChanges {
  @Input({ required: true }) hubUrl = '';

  private readonly api = inject(VoiceApiService);
  private readonly cdr = inject(ChangeDetectorRef);

  capabilities: VoiceCapabilityStatus | null = null;
  loading = false;
  errorCode = '';
  errorMessage = '';

  ngOnChanges(): void {
    if (this.hubUrl) this.load();
  }

  load(): void {
    this.loading = true;
    this.errorCode = '';
    this.errorMessage = '';
    this.api.getCapabilities(this.hubUrl).subscribe({
      next: (capabilities) => {
        this.capabilities = capabilities;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        const detail = voiceError(error);
        this.loading = false;
        this.errorCode = detail.code;
        this.errorMessage = detail.message;
        this.cdr.markForCheck();
      },
    });
  }

  models(): VoiceModelCapability[] {
    return this.capabilities?.models || [];
  }

  resourceRows(): Array<{ label: string; value: string }> {
    const resources = this.capabilities?.resources || this.capabilities?.health?.resources;
    const rows = Array.isArray(resources) ? resources : resources ? [resources] : [];
    return rows.flatMap((resource, index) => Object.entries(resource)
      .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value) && value != null)
      .map(([key, value]) => ({
        label: `${resource.name || `Ressource ${index + 1}`} · ${key}`,
        value: String(value),
      })));
  }
}
