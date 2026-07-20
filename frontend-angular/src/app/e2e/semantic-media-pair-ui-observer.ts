import { ApplicationRef, ComponentRef, EnvironmentInjector, createComponent } from '@angular/core';

import {
  PeerEvidenceOfferView,
  PeerEvidenceSyncPanelComponent,
} from '../features/voice/peer-evidence-sync-panel.component';

/**
 * Mount the actual product panel in the dedicated E2E application and return
 * a content-free observation. No raw candidate is accepted by this boundary.
 */
export function createPeerEvidencePreviewObserver(
  application: ApplicationRef,
  injector: EnvironmentInjector,
): (offer: PeerEvidenceOfferView) => boolean {
  let mounted: ComponentRef<PeerEvidenceSyncPanelComponent> | null = null;
  return offer => {
    mounted?.destroy();
    document.querySelector('[data-semantic-pair-e2e-preview]')?.remove();
    const host = document.createElement('div');
    host.dataset['semanticPairE2ePreview'] = 'true';
    document.body.append(host);
    mounted = createComponent(PeerEvidenceSyncPanelComponent, {
      environmentInjector: injector,
      hostElement: host,
    });
    mounted.setInput('offer', offer);
    mounted.setInput('hubUrl', 'e2e-content-free');
    mounted.setInput('availableReason', 'Signierte Produktpreview');
    application.attachView(mounted.hostView);
    mounted.changeDetectorRef.detectChanges();
    const projection = host.querySelector('[data-testid="peer-evidence-comparison-preview"]');
    const offerProjection = host.querySelector('[data-testid="peer-evidence-offer"]');
    const text = host.textContent || '';
    return Boolean(
      projection
      && offerProjection
      && text.includes('Inhaltsfreie Originalkandidaten')
      && text.includes('Original 1')
      && text.includes('Original 2')
      && text.includes(`Resolution: ${offer.groupPreviews[0]?.resolutionState}`)
      && !text.includes('ANANTA_DIRECT_CANARY_')
      && !text.includes('ANANTA_RELAY_CANARY_')
    );
  };
}
