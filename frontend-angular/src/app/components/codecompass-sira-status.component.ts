import { Component, Input, OnChanges, inject } from '@angular/core';
import { JsonPipe } from '@angular/common';
import {
  CodeCompassSiraApiService,
  CodeCompassSiraStatus,
} from '../services/codecompass-sira-api.service';

@Component({
  standalone: true,
  selector: 'app-codecompass-sira-status',
  imports: [JsonPipe],
  template: `
    <div class="row flex-between">
      <div>
        <h3 class="no-margin">CodeCompass Corpus-Discriminative Retrieval</h3>
        <div class="muted font-sm">SIRA-inspiriertes, optionales Profil im bestehenden FTS-/Hybridpfad</div>
      </div>
      <button class="button-outline btn-xs" (click)="load()" [disabled]="loading">Aktualisieren</button>
    </div>
    @if (loading) {
      <p class="muted">Status wird geladen …</p>
    } @else if (error) {
      <div class="state-banner error">{{ error }}</div>
    } @else if (status) {
      <div class="grid cols-4 mt-sm">
        <div><div class="muted">Status</div><strong>{{ status.status }}</strong></div>
        <div><div class="muted">Modus</div><strong>{{ status.rollout.mode }}</strong></div>
        <div><div class="muted">Index</div><strong>{{ status.index['status'] || 'unbekannt' }}</strong></div>
        <div><div class="muted">Artefakte</div><strong>{{ status.index['artifact_count'] ?? '—' }}</strong></div>
      </div>
      <div class="muted font-sm mt-sm">
        Konfiguration: {{ status.config_reason }} · Index: {{ status.index['reason'] || '—' }}
      </div>
      @if (status.rollout.shadow_non_effecting) {
        <div class="state-banner mt-sm">Shadow-Ergebnisse beeinflussen die produktive Auswahl nicht.</div>
      }
      <details class="mt-sm">
        <summary>Versionen und Kill-Switches</summary>
        <pre class="font-sm">{{ { config: status.config, index: status.index, rollout: status.rollout } | json }}</pre>
      </details>
    }
  `,
})
export class CodeCompassSiraStatusComponent implements OnChanges {
  @Input({ required: true }) baseUrl = '';

  private readonly api = inject(CodeCompassSiraApiService);
  status: CodeCompassSiraStatus | null = null;
  loading = false;
  error = '';

  ngOnChanges(): void {
    if (this.baseUrl) this.load();
  }

  load(): void {
    if (!this.baseUrl || this.loading) return;
    this.loading = true;
    this.error = '';
    this.api.status(this.baseUrl).subscribe({
      next: (response) => {
        this.status = response.data;
        this.loading = false;
      },
      error: () => {
        this.error = 'SIRA-Status konnte nicht geladen werden.';
        this.loading = false;
      },
    });
  }
}
