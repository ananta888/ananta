import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';

import { PairComputeContractPanelComponent } from './pair-compute-contract-panel.component';

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('PairComputeContractPanelComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [PairComputeContractPanelComponent] }).compileComponents();
  });

  it('separates local, peer, Hub and lease sources semantically', () => {
    const fixture = TestBed.createComponent(PairComputeContractPanelComponent);
    fixture.componentRef.setInput('contract', {
      contractId: 'contract-a', digest: 'a'.repeat(64), revision: 4, status: 'active', profile: 'balanced', delayMs: 5_000,
      expiresAtMs: 20_000, reasonCode: 'activate_accepted', roles: { primary: ['peer-a'] },
    });
    fixture.componentRef.setInput('localMeasurement', { cpu: 'medium', memory: 'medium', gpu: 'unknown', codec: 'unknown', battery: 'mains', network: 'normal' });
    fixture.componentRef.setInput('peerClaim', { cpu: 'high', memory: 'high', gpu: 'dedicated', codec: 'hardware', battery: 'mains', network: 'fast' });
    fixture.componentRef.setInput('leases', [{
      leaseId: 'lease-a', contractDigest: 'a'.repeat(64), role: 'primary', executorId: 'peer-a',
      status: 'active', expiresAtMs: 15_000,
    }]);
    fixture.componentRef.setInput('nowMs', 10_000);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector('[aria-label="Lokale Messung"]')).toBeTruthy();
    expect(element.querySelector('[aria-label="Peer-Selbstauskunft"]')?.textContent).toContain('nicht autoritativ');
    expect(element.querySelector('[aria-label="Hub-Entscheidung"]')?.textContent).toContain('balanced');
    expect(element.querySelector('[aria-label="Hub-Leases"]')?.textContent).toContain('aktiv');
  });

  it('never presents stale Hub data or leases as active', () => {
    const fixture = TestBed.createComponent(PairComputeContractPanelComponent);
    fixture.componentRef.setInput('contract', {
      contractId: 'contract-a', revision: 2, status: 'active', profile: 'balanced', delayMs: 5_000,
      expiresAtMs: 9_000, roles: {},
    });
    fixture.componentRef.setInput('nowMs', 10_000);
    fixture.detectChanges();
    expect(fixture.componentInstance.authoritativeActive).toBe(false);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('veraltete Ansicht');
  });

  it('does not present a lease from an older contract digest as active', () => {
    const fixture = TestBed.createComponent(PairComputeContractPanelComponent);
    fixture.componentRef.setInput('contract', {
      contractId: 'contract-a', digest: 'b'.repeat(64), revision: 3, status: 'active',
      profile: 'balanced', delayMs: 5_000, expiresAtMs: 20_000, roles: {},
    });
    const lease = {
      leaseId: 'lease-old', contractDigest: 'a'.repeat(64), role: 'primary' as const,
      executorId: 'peer-a', status: 'active' as const, expiresAtMs: 15_000,
    };
    fixture.componentRef.setInput('leases', [lease]);
    fixture.componentRef.setInput('nowMs', 10_000);
    fixture.detectChanges();
    expect(fixture.componentInstance.leaseActive(lease)).toBe(false);
    expect((fixture.nativeElement as HTMLElement).querySelector('[aria-label="Hub-Leases"]')?.textContent)
      .not.toContain('aktiv');
  });

  it('emits bounded keyboard-operable intents with the authoritative revision', () => {
    const fixture = TestBed.createComponent(PairComputeContractPanelComponent);
    fixture.componentRef.setInput('contract', {
      contractId: 'contract-a', revision: 7, status: 'active', profile: 'custom', delayMs: 8_000,
      expiresAtMs: 20_000, roles: {},
    });
    const emitted = vi.fn();
    fixture.componentInstance.intent.subscribe(emitted);
    fixture.componentInstance.selectedDelayMs = 20_000;
    fixture.componentInstance.emitDelay();
    fixture.componentInstance.emit('revoke');
    expect(emitted).toHaveBeenNthCalledWith(1, { kind: 'delay', expectedRevision: 7, value: 20_000 });
    expect(emitted).toHaveBeenNthCalledWith(2, { kind: 'revoke', expectedRevision: 7, value: undefined });
    fixture.componentInstance.selectedDelayMs = 20_001;
    fixture.componentInstance.emitDelay();
    expect(emitted).toHaveBeenCalledTimes(2);
  });

  it('labels AI-Snake suggestions non-authoritative and applies them only as normal revisioned intents', () => {
    const fixture = TestBed.createComponent(PairComputeContractPanelComponent);
    fixture.componentRef.setInput('contract', {
      contractId: 'contract-a', revision: 8, status: 'active', profile: 'balanced', delayMs: 5_000,
      expiresAtMs: Date.now() + 10_000, roles: {},
    });
    fixture.componentRef.setInput('suggestion', {
      profile: 'conservative', delayMs: 8_000, rationale: 'Last reduzieren',
      authoritative: false, requiresSeparateHubMutation: true,
    });
    const emitted = vi.fn();
    fixture.componentInstance.intent.subscribe(emitted);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('nicht autoritativ');
    fixture.componentInstance.applySuggestedProfile();
    fixture.componentInstance.applySuggestedDelay();
    expect(emitted).toHaveBeenNthCalledWith(1, {
      kind: 'profile', expectedRevision: 8, value: 'conservative',
    });
    expect(emitted).toHaveBeenNthCalledWith(2, { kind: 'delay', expectedRevision: 8, value: 8_000 });
  });
});
