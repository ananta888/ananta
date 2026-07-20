import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  SPEECH_EVIDENCE_GRANTS,
  SpeechEvidenceDataClass,
  SpeechEvidenceGrant,
} from '../../services/speech-evidence-consent-api.service';
import {
  SpeechEvidenceConsentDraft,
  SpeechEvidenceConsentIntent,
  SpeechEvidenceConsentPanelState,
} from './speech-evidence-consent.facade';

const DATA_CLASSES: readonly SpeechEvidenceDataClass[] = [
  'transcript', 'correction', 'acoustic_features', 'quality_metrics', 'speaker_embedding', 'audio',
];

@Component({
  selector: 'app-speech-evidence-consent-panel',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-labelledby="speech-consent-title">
      <h3 id="speech-consent-title">Hub-Sprachfreigabe</h3>
      <p>Jeder Grant bleibt einzeln, zweckgebunden und Hub-versioniert. Signatur-Digests werden nie lokal erfunden.</p>
      <div class="lookup">
        <label>Consent-ID <input [(ngModel)]="consentId" maxlength="160" /></label>
        <button type="button" [disabled]="state.pending || !validId(consentId)" (click)="load()">Vom Hub laden</button>
      </div>
      @if (!state.bound) { <p role="alert">Kein bestätigter Pair-/Hub-Kontext.</p> }
      @if (state.errorCode) { <p role="alert">{{ state.errorCode }}</p> }
      @if (state.consent; as current) {
        <p role="status">Hub: {{ current.consent.state }} · Version {{ current.consent.consent_version }} · Revocation {{ current.consent.revocation_epoch }}</p>
        <p>Consent-Digest {{ current.consentDigest }}<br />Scope-Digest {{ current.scopeDigest }}</p>
      }
      <div class="grid">
        <label>Zweck <input [(ngModel)]="purpose" maxlength="160" /></label>
        <label>Retention in Sekunden <input type="number" min="60" max="31536000" [(ngModel)]="retentionSeconds" /></label>
        <label>Gültigkeit in Stunden <input type="number" min="1" max="8784" [(ngModel)]="expiresInHours" /></label>
        <label>Trainerstandorte, komma-separiert <input [(ngModel)]="trainerLocationsText" /></label>
      </div>
      <fieldset>
        <legend>Datenklassen</legend>
        @for (name of dataClasses; track name) {
          <label><input type="checkbox" [checked]="selectedDataClasses.has(name)"
            (change)="toggleDataClass(name, $any($event.target).checked)" /> {{ name }}</label>
        }
      </fieldset>
      <fieldset>
        <legend>Einzelne Grants</legend>
        @for (name of grants; track name) {
          <label><input type="checkbox" [checked]="grantSelection[name]"
            (change)="grantSelection[name] = $any($event.target).checked" /> {{ name }}</label>
        }
      </fieldset>
      @if (!state.consent) {
        <fieldset>
          <legend>Extern bestätigte bilaterale Signatur-Digests</legend>
          @for (signer of state.signerIds; track signer) {
            <label>{{ signer }} <input [ngModel]="signerDigests[signer] || ''"
              (ngModelChange)="signerDigests[signer] = $event" maxlength="64" /></label>
          }
        </fieldset>
      }
      <div class="actions">
        <button type="button" [disabled]="!canGrant()" (click)="submit('grant')">Neu freigeben</button>
        <button type="button" [disabled]="!canMutate()" (click)="submit('reduce')">Umfang reduzieren</button>
        <button type="button" [disabled]="!canMutate()" (click)="submit('renew')">Erneuern</button>
        <button type="button" [disabled]="!canMutate()" (click)="intent.emit({ kind: 'revoke' })">Widerrufen</button>
      </div>
    </section>
  `,
  styles: [`
    :host { display:block; margin-top:1rem; } section { border:1px solid currentColor; border-radius:.6rem; padding:.8rem; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(12rem,1fr)); gap:.6rem; }
    .lookup,.actions { display:flex; align-items:end; flex-wrap:wrap; gap:.6rem; }
    label { display:grid; gap:.2rem; } fieldset { display:flex; flex-wrap:wrap; gap:.6rem; margin-block:.7rem; }
    input,button { min-height:2.5rem; } p { overflow-wrap:anywhere; }
  `],
})
export class SpeechEvidenceConsentPanelComponent implements OnChanges {
  @Input({ required: true }) state!: SpeechEvidenceConsentPanelState;
  @Output() readonly intent = new EventEmitter<SpeechEvidenceConsentIntent>();
  readonly dataClasses = DATA_CLASSES;
  readonly grants = SPEECH_EVIDENCE_GRANTS;
  consentId = '';
  purpose = 'speech_quality_improvement';
  retentionSeconds = 3_600;
  expiresInHours = 24;
  trainerLocationsText = '';
  readonly selectedDataClasses = new Set<SpeechEvidenceDataClass>(['transcript', 'correction']);
  grantSelection = defaultGrants();
  signerDigests: Record<string, string> = {};

  ngOnChanges(): void {
    const consent = this.state?.consent?.consent;
    if (!consent) return;
    this.consentId = consent.consent_id;
    this.purpose = consent.purpose;
    this.retentionSeconds = consent.retention_seconds;
    this.expiresInHours = Math.max(1, Math.ceil((consent.expires_at_ms - Date.now()) / 3_600_000));
    this.trainerLocationsText = consent.trainer_locations.join(', ');
    this.selectedDataClasses.clear();
    for (const value of consent.data_classes) this.selectedDataClasses.add(value);
    this.grantSelection = { ...consent.grants };
    this.signerDigests = { ...consent.signatures };
  }

  toggleDataClass(value: SpeechEvidenceDataClass, enabled: boolean): void {
    if (enabled) this.selectedDataClasses.add(value); else this.selectedDataClasses.delete(value);
  }

  load(): void { if (this.validId(this.consentId)) this.intent.emit({ kind: 'load', consentId: this.consentId }); }

  submit(kind: 'grant' | 'reduce' | 'renew'): void {
    const draft = this.draft();
    if (!draft) return;
    this.intent.emit({ kind, draft });
  }

  canGrant(): boolean {
    return this.state?.bound && !this.state.pending && !this.state.consent && this.draft() !== null
      && this.state.signerIds.every(id => /^[0-9a-f]{64}$/.test(this.signerDigests[id] ?? ''));
  }

  canMutate(): boolean { return Boolean(this.state?.consent && !this.state.pending && this.draft()); }
  validId(value: string): boolean { return /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$/.test(value); }

  private draft(): SpeechEvidenceConsentDraft | null {
    const trainers = this.trainerLocationsText.split(',').map(value => value.trim()).filter(Boolean);
    if (!this.validId(this.consentId) || !this.validId(this.purpose)
      || !Number.isSafeInteger(this.retentionSeconds) || this.retentionSeconds < 60 || this.retentionSeconds > 31_536_000
      || !Number.isSafeInteger(this.expiresInHours) || this.expiresInHours < 1 || this.expiresInHours > 8_784
      || !this.selectedDataClasses.size
      || (this.grantSelection.training && !trainers.length)
      || (this.grantSelection.raw_audio_share && !this.selectedDataClasses.has('audio'))
    ) return null;
    return Object.freeze({
      consentId: this.consentId,
      purpose: this.purpose,
      dataClasses: Object.freeze([...this.selectedDataClasses]),
      retentionSeconds: this.retentionSeconds,
      trainerLocations: Object.freeze(trainers),
      grants: Object.freeze({ ...this.grantSelection }),
      expiresInHours: this.expiresInHours,
      signerDigests: Object.freeze({ ...this.signerDigests }),
    });
  }
}

function defaultGrants(): Record<SpeechEvidenceGrant, boolean> {
  return Object.fromEntries(SPEECH_EVIDENCE_GRANTS.map(name => [
    name, name === 'capture' || name === 'transcript_share',
  ])) as Record<SpeechEvidenceGrant, boolean>;
}
