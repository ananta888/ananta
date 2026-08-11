import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';

import {
  PublicPairMediaPublicationConsentState,
  PublicPairMediaPublicationConsentTerm,
} from '../../services/public-pair-media-publication-consent.service';

type ConsentTermSelection = 'session' | '15m' | '60m';

@Component({
  selector: 'app-public-pair-media-publication-consent-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      aria-labelledby="public-pair-publication-consent-title"
      data-capability="ordinary_media"
      data-testid="public-pair-publication-consent">
      <h3 id="public-pair-publication-consent-title">Meine Medienfreigabe</h3>
      <p id="public-pair-publication-consent-scope">
        Ich erlaube in dieser Pair-Sitzung die Übertragung meines Mikrofons, meiner Kamera und
        meines Bildschirms. Die Aufnahme startet erst nach einem weiteren Klick auf die gewünschte Quelle.
      </p>
      <p role="note">
        Der Empfang der Medien des Gegenübers ist davon unabhängig. Der Chat bleibt beim Aktivieren,
        Deaktivieren und Ablaufen der eigenen Freigabe verbunden.
      </p>

      <fieldset [disabled]="selectionLocked()" aria-describedby="public-pair-publication-consent-scope">
        <legend>Gültigkeit der Einwilligung</legend>
        <label>
          <input
            type="radio"
            name="public-pair-publication-consent-term"
            value="session"
            [checked]="termSelection === 'session'"
            (change)="selectTerm('session')" />
          Bis zum Ende dieser Pair-Sitzung oder bis zum Widerruf
        </label>
        <label>
          <input
            type="radio"
            name="public-pair-publication-consent-term"
            value="15m"
            [checked]="termSelection === '15m'"
            (change)="selectTerm('15m')" />
          15 Minuten
        </label>
        <label>
          <input
            type="radio"
            name="public-pair-publication-consent-term"
            value="60m"
            [checked]="termSelection === '60m'"
            (change)="selectTerm('60m')" />
          1 Stunde
        </label>
      </fieldset>

      <p
        id="public-pair-publication-consent-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid="public-pair-publication-consent-status">
        {{ statusLabel() }}
        @if (state.expiresAtMs; as expiresAtMs) {
          <time [attr.datetime]="expiryIso(expiresAtMs)">{{ expiryLabel(expiresAtMs) }}</time>
        }
      </p>
      @if (state.reasonCode) {
        <p [attr.role]="state.status === 'failed' ? 'alert' : 'note'">
          {{ reasonLabel(state.reasonCode) }} <code>{{ state.reasonCode }}</code>
        </p>
      }

      @if (consentPresent()) {
        <button
          type="button"
          [disabled]="pending()"
          aria-pressed="true"
          aria-describedby="public-pair-publication-consent-scope public-pair-publication-consent-status"
          (click)="revoke.emit()">
          Eigene Freigabe deaktivieren
        </button>
      } @else if (resetRequired()) {
        <button
          type="button"
          [disabled]="pending()"
          aria-pressed="false"
          aria-describedby="public-pair-publication-consent-scope public-pair-publication-consent-status"
          (click)="revoke.emit()">
          Sicher zurücksetzen
        </button>
      } @else {
        <button
          type="button"
          [disabled]="!canGrant()"
          aria-pressed="false"
          aria-describedby="public-pair-publication-consent-scope public-pair-publication-consent-status"
          (click)="grant.emit(selectedTerm())">
          Einwilligen und Medien aktivieren
        </button>
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    section { border: 2px solid currentColor; border-radius: .6rem; padding: .8rem; }
    fieldset { display: grid; gap: .55rem; margin-block: .75rem; }
    label { display: flex; gap: .55rem; align-items: flex-start; }
    input { inline-size: 1.25rem; block-size: 1.25rem; flex: 0 0 auto; }
    button { min-height: 2.75rem; }
    button:focus-visible, input:focus-visible { outline: 3px solid currentColor; outline-offset: 2px; }
    p { overflow-wrap: anywhere; }
  `],
})
export class PublicPairMediaPublicationConsentPanelComponent implements OnChanges {
  @Input({ required: true }) state!: PublicPairMediaPublicationConsentState;
  @Input() technicalPreparationAvailable = false;
  @Input() technicalPreparationPending = false;
  @Output() readonly grant = new EventEmitter<PublicPairMediaPublicationConsentTerm>();
  @Output() readonly revoke = new EventEmitter<void>();

  termSelection: ConsentTermSelection = 'session';

  ngOnChanges(): void {
    const term = this.state?.term;
    if (term?.kind === 'session') this.termSelection = 'session';
    if (term?.kind === 'timed') this.termSelection = term.durationMs === 900_000 ? '15m' : '60m';
  }

  selectTerm(value: ConsentTermSelection): void {
    if (!this.selectionLocked()) this.termSelection = value;
  }

  selectedTerm(): PublicPairMediaPublicationConsentTerm {
    if (this.termSelection === 'session') return Object.freeze({ kind: 'session' });
    return Object.freeze({
      kind: 'timed',
      durationMs: this.termSelection === '15m' ? 900_000 : 3_600_000,
    });
  }

  pending(): boolean {
    return this.state.status === 'granting' || this.state.status === 'revoking';
  }

  consentPresent(): boolean {
    return this.state.status === 'granted' || this.state.status === 'revoking';
  }

  resetRequired(): boolean {
    return this.state.status === 'failed' && this.state.binding !== null;
  }

  selectionLocked(): boolean {
    return this.pending()
      || this.preparationPending()
      || this.state.status === 'granted'
      || this.resetRequired();
  }

  canGrant(): boolean {
    if (this.pending() || this.resetRequired()) return false;
    if (this.state.binding !== null) {
      return ['inactive', 'revoked', 'expired'].includes(this.state.status);
    }
    return this.state.status === 'unbound'
      && this.technicalPreparationAvailable
      && !this.preparationPending();
  }

  preparationPending(): boolean {
    return this.state.status === 'unbound' && this.technicalPreparationPending;
  }

  statusLabel(): string {
    if (this.preparationPending()) {
      return 'Der verschlüsselte Medienpfad wird vorbereitet. Eigene Medien bleiben gesperrt.';
    }
    switch (this.state.status) {
      case 'unbound': return 'Noch keine gültige Public-Pair-Medienbindung.';
      case 'inactive': return 'Eigene Medien sind nicht freigegeben.';
      case 'granting': return 'Die eigene Einwilligung wird sicher aktiviert.';
      case 'granted': return 'Eigene Medien sind freigegeben bis';
      case 'revoking': return 'Die eigene Freigabe wird deaktiviert. Der Chat bleibt verbunden.';
      case 'revoked': return 'Eigene Medien sind deaktiviert. Der Chat bleibt verbunden.';
      case 'expired': return 'Die eigene Einwilligung ist abgelaufen. Der Chat bleibt verbunden.';
      case 'failed': return 'Die eigene Medienfreigabe konnte nicht geändert werden.';
    }
  }

  expiryIso(expiresAtMs: number): string {
    return new Date(expiresAtMs).toISOString();
  }

  expiryLabel(expiresAtMs: number): string {
    return new Intl.DateTimeFormat('de-DE', {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(new Date(expiresAtMs));
  }

  reasonLabel(reasonCode: string): string {
    return ({
      public_media_publication_consent_unbound:
        'Die exakte Pair-Sitzung oder der signierte Medienvertrag ist noch nicht gebunden.',
      public_media_publication_consent_required:
        'Vor dem Senden eigener Medien ist eine ausdrückliche Einwilligung erforderlich.',
      public_media_publication_consent_granting:
        'Die lokale Sendefreigabe wird sicher eingerichtet.',
      public_media_publication_consent_expired:
        'Die gewählte Zeit ist abgelaufen; die Freigabe kann erneut erteilt werden.',
      public_media_publication_consent_revoked:
        'Die eigene Einwilligung wurde widerrufen.',
      public_media_publication_consent_gate_failed:
        'Der ausgehende Medienpfad konnte nicht sicher geschaltet werden.',
    } as Record<string, string>)[reasonCode] ?? 'Technischer Grund der lokalen Medienfreigabe:';
  }
}
