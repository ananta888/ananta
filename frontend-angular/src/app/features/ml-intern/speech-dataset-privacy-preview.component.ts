import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  Input,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, finalize } from 'rxjs';

import {
  SpeechDatasetPrivacyPreview,
  SpeechDatasetPrivacyPreviewApiService,
} from '../../services/speech-dataset-privacy-preview-api.service';

@Component({
  selector: 'app-speech-dataset-privacy-preview',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="speech-dataset-preview-title" data-testid="speech-dataset-privacy-preview">
      <h3 id="speech-dataset-preview-title">Dataset-Datenschutzvorschau</h3>
      <p>Aggregierte, content-freie Übersicht vor Split oder Training.</p>
      @if (loading) { <p role="status" aria-live="polite">Vorschau wird geladen …</p> }
      @if (errorCode) { <p role="alert">{{ errorCode }}</p> }
      @if (preview; as row) {
        <dl>
          <dt>Dataset</dt><dd>{{ row.datasetId }}</dd>
          <dt>Manifest</dt><dd>{{ row.manifestDigest }}</dd>
          <dt>Records</dt><dd>{{ row.recordCount }}</dd>
          <dt>Dauer</dt><dd>{{ row.totalDurationMs }} ms</dd>
          <dt>Quarantäne</dt><dd>{{ row.quarantineCount }}</dd>
          <dt>Datenklassen</dt><dd>{{ pairs(row.dataClassCounts) }}</dd>
          <dt>Contributor-Scopes</dt><dd>{{ pairs(row.contributorScopes) }}</dd>
          <dt>Consent-Grants</dt><dd>{{ pairs(row.grantRefCounts) }}</dd>
          <dt>Scanbefunde</dt><dd>{{ pairs(row.scanFindings) }}</dd>
        </dl>

        <fieldset>
          <legend>Separate Raw-Audio-Vorschaufreigabe</legend>
          <p role="note">Raw Audio wird nie automatisch geladen. Eine eigene, serverseitig geprüfte Freigabe ist erforderlich.</p>
          <label>Preview-Grant
            <input type="password" autocomplete="off" [(ngModel)]="rawPreviewGrant"
              [disabled]="loading" aria-describedby="speech-preview-grant-note" />
          </label>
          <span id="speech-preview-grant-note">Der Grant wird nur für diesen expliziten Abruf verwendet und nicht gespeichert.</span>
          <button type="button" [disabled]="loading || rawPreviewGrant.trim().length < 8"
            (click)="requestRawPreview()">Raw-Audio-Vorschau ausdrücklich anfordern</button>
        </fieldset>

        @if (row.rawAudioPreview.authorized) {
          <p role="status">Raw-Audio-Vorschau autorisiert: {{ row.rawAudioPreview.refs.length }} begrenzte Referenzen.</p>
          <ul>
            @for (ref of row.rawAudioPreview.refs; track ref) { <li>{{ ref }}</li> }
          </ul>
        } @else {
          <p>Raw-Audio-Vorschau nicht freigegeben.</p>
        }
      }
    </section>
  `,
  styles: [`
    :host{display:block;margin-top:1rem}
    section{border:1px solid var(--border-color,#777);border-radius:.5rem;padding:1rem}
    dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem .75rem}
    dt{font-weight:600} dd,li{overflow-wrap:anywhere}
    fieldset{display:grid;gap:.5rem;margin-top:1rem} input,button{min-height:2.75rem}
  `],
})
export class SpeechDatasetPrivacyPreviewComponent implements OnChanges, OnDestroy {
  @Input() hubUrl = '';
  @Input() manifestDigest: string | null = null;

  private readonly api = inject(SpeechDatasetPrivacyPreviewApiService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private request = new Subscription();
  private contextKey = '';

  preview: SpeechDatasetPrivacyPreview | null = null;
  loading = false;
  errorCode: string | null = null;
  rawPreviewGrant = '';

  ngOnChanges(_changes: SimpleChanges): void {
    const key = `${this.hubUrl}\0${this.manifestDigest ?? ''}`;
    if (key === this.contextKey) return;
    this.contextKey = key;
    this.request.unsubscribe();
    this.request = new Subscription();
    this.preview = null;
    this.errorCode = null;
    this.rawPreviewGrant = '';
    if (!this.validContext()) return;
    this.load(false);
  }

  ngOnDestroy(): void { this.request.unsubscribe(); }

  requestRawPreview(): void {
    if (!this.validContext() || this.rawPreviewGrant.trim().length < 8 || this.loading) return;
    this.load(true);
  }

  pairs(values: Readonly<Record<string, number>>): string {
    const rows = Object.entries(values).sort(([left], [right]) => left.localeCompare(right));
    return rows.length ? rows.map(([key, count]) => `${key}: ${count}`).join(', ') : 'keine';
  }

  private load(includeRawAudio: boolean): void {
    const digest = String(this.manifestDigest);
    const context = this.contextKey;
    this.loading = true;
    this.errorCode = null;
    const source = includeRawAudio
      ? this.api.withRawAudioGrant(this.hubUrl, digest, this.rawPreviewGrant.trim())
      : this.api.aggregate(this.hubUrl, digest);
    const child = source.pipe(finalize(() => {
      if (context !== this.contextKey) return;
      this.loading = false;
      this.changeDetector.markForCheck();
    })).subscribe({
      next: preview => {
        if (context !== this.contextKey) return;
        this.preview = preview;
        if (includeRawAudio) this.rawPreviewGrant = '';
      },
      error: error => {
        if (context !== this.contextKey) return;
        this.errorCode = reason(error);
      },
    });
    this.request.add(child);
  }

  private validContext(): boolean {
    return /^https?:\/\/[^\s]+$/.test(this.hubUrl.trim())
      && /^[0-9a-f]{64}$/.test(String(this.manifestDigest ?? ''));
  }
}

function reason(error: unknown): string {
  const value = error as { error?: { error?: { code?: unknown } }; message?: unknown };
  const code = value?.error?.error?.code;
  return typeof code === 'string' && code ? code : String(value?.message || 'speech_preview_unavailable');
}
