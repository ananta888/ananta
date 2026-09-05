import { Component, inject } from '@angular/core';

import { PeerEdgePrivacyMode } from './peer-edge-network-policy';
import { PeerOverlayPrivacyPreferenceService } from './peer-overlay-privacy-preference.service';

@Component({
  selector: 'app-peer-overlay-privacy-settings',
  standalone: true,
  template: `
    <section class="card" aria-labelledby="peer-privacy-title">
      <h3 id="peer-privacy-title">Peer-Verbindung und IP-Datenschutz</h3>
      <p>Wähle die Transportstrategie für jede neue, vom Hub autorisierte Peer-Kante.</p>
      @for (option of options; track option.value) {
        <label class="privacy-option">
          <input type="radio" name="peer-privacy" [value]="option.value"
            [checked]="mode === option.value" (change)="select(option.value)" />
          <span><strong>{{ option.label }}</strong><br />{{ option.description }}</span>
        </label>
      }
      <p class="privacy-warning" data-testid="peer-ip-disclosure">
        mDNS und Ende-zu-Ende-Verschlüsselung verbergen die verwendete IP nicht vor einem direkt verbundenen Nachbarn.
        Relay-only erzwingt TURN, kann Kosten und Latenz erhöhen und funktioniert nicht, wenn TURN unerreichbar ist.
      </p>
      <p>Die Teilnahme dieses Browsers als Relay ist davon getrennt. Sie wird pro Raum ausdrücklich,
        zeitlich begrenzt und widerrufbar erteilt; ohne gültigen Consent bleibt der Browser ein Blatt.</p>
    </section>
  `,
  styles: [`
    .privacy-option { display:flex; gap:.75rem; margin:.75rem 0; align-items:flex-start; }
    .privacy-warning { border-left:3px solid var(--warning, #b7791f); padding-left:.75rem; }
  `],
})
export class PeerOverlayPrivacySettingsComponent {
  private readonly preference = inject(PeerOverlayPrivacyPreferenceService);
  readonly options: ReadonlyArray<{ value: PeerEdgePrivacyMode; label: string; description: string }> = [
    { value: 'direct_preferred', label: 'Direkt bevorzugt', description: 'Versucht direkte Kandidaten; TURN bleibt kantenlokaler Fallback.' },
    { value: 'automatic', label: 'Automatisch', description: 'Der Hub wählt je Kante anhand von Policy und Erreichbarkeit.' },
    { value: 'relay_only', label: 'Nur Relay', description: 'Erzwingt TURN und vermeidet eine direkte Peer-Verbindung.' },
  ];
  mode = this.preference.mode;

  select(mode: PeerEdgePrivacyMode): void {
    this.preference.setMode(mode);
    this.mode = mode;
  }
}
