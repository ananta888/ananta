import { Injectable } from '@angular/core';

import { PeerEdgePrivacyMode } from './peer-edge-network-policy';

const STORAGE_KEY = 'ananta.peer-overlay.privacy-mode.v1';
const MODES: readonly PeerEdgePrivacyMode[] = ['direct_preferred', 'automatic', 'relay_only'];

@Injectable({ providedIn: 'root' })
export class PeerOverlayPrivacyPreferenceService {
  private selected = this.read();

  get mode(): PeerEdgePrivacyMode { return this.selected; }

  setMode(mode: PeerEdgePrivacyMode): void {
    if (!MODES.includes(mode)) throw new Error('peer_edge_privacy_mode_invalid');
    this.selected = mode;
    try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* memory preference remains effective */ }
  }

  private read(): PeerEdgePrivacyMode {
    try {
      const stored = localStorage.getItem(STORAGE_KEY) as PeerEdgePrivacyMode | null;
      return stored && MODES.includes(stored) ? stored : 'automatic';
    } catch {
      return 'automatic';
    }
  }
}
