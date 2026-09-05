import { TestBed } from '@angular/core/testing';

import { PeerOverlayPrivacyPreferenceService } from './peer-overlay-privacy-preference.service';
import { PeerOverlayPrivacySettingsComponent } from './peer-overlay-privacy-settings.component';

describe('PeerOverlayPrivacySettingsComponent', () => {
  beforeEach(() => localStorage.clear());

  it('shows all privacy modes and accurately warns about IP visibility and TURN', async () => {
    await TestBed.configureTestingModule({ imports: [PeerOverlayPrivacySettingsComponent] }).compileComponents();
    const fixture = TestBed.createComponent(PeerOverlayPrivacySettingsComponent);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Direkt bevorzugt');
    expect(text).toContain('Automatisch');
    expect(text).toContain('Nur Relay');
    expect(text).toContain('mDNS und Ende-zu-Ende-Verschlüsselung verbergen');
    expect(text).toContain('Relay-only erzwingt TURN');
    expect(text).toContain('widerrufbar');
  });

  it('persists the selected edge policy without granting relay consent', () => {
    const preference = new PeerOverlayPrivacyPreferenceService();
    preference.setMode('relay_only');
    expect(new PeerOverlayPrivacyPreferenceService().mode).toBe('relay_only');
    expect(localStorage.length).toBe(1);
  });
});
