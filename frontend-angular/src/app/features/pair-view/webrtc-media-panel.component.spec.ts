import { describe, expect, it } from 'vitest';

import { WebrtcMediaPanelComponent } from './webrtc-media-panel.component';

describe('WebrtcMediaPanelComponent Public Pair reasons', () => {
  it.each([
    ['public_ordinary_media_e2ee_not_ready', 'noch nicht bereit'],
    ['public_ordinary_media_e2ee_awaiting_peer', 'ebenfalls aktivieren'],
    ['media_e2ee_transform_unsupported', 'unterstützt den benötigten Medien-E2EE-Pfad nicht'],
    ['public_media_runtime_unsupported', 'standardisierten Public-Medien-E2EE-Pfad'],
    ['public_media_fresh_connection_required', 'neuen Pair-Session'],
    ['media_e2ee_authentication_failed', 'konnte nicht authentifiziert werden'],
    ['ordinary_media_public_authority_offline', 'Public-Pair-Autorität ist nicht erreichbar'],
  ])('maps %s to an actionable label', (reasonCode, expected) => {
    expect(new WebrtcMediaPanelComponent().reasonLabel(reasonCode)).toContain(expected);
  });

  it('keeps unknown codes visible under a Pair-authority fallback label', () => {
    expect(new WebrtcMediaPanelComponent().reasonLabel('media_e2ee_future_failure'))
      .toBe('Medienstatus der Pair-Autorität oder des Browsers:');
  });
});
