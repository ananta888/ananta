import { ComponentFixture, TestBed } from '@angular/core/testing';

import { PublicPairMediaPublicationConsentState } from '../../services/public-pair-media-publication-consent.service';
import { PublicPairMediaPublicationConsentPanelComponent } from './public-pair-media-publication-consent-panel.component';

describe('PublicPairMediaPublicationConsentPanelComponent', () => {
  let fixture: ComponentFixture<PublicPairMediaPublicationConsentPanelComponent>;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [PublicPairMediaPublicationConsentPanelComponent] });
    fixture = TestBed.createComponent(PublicPairMediaPublicationConsentPanelComponent);
    fixture.componentRef.setInput('state', state());
    fixture.detectChanges();
  });

  it('states the outbound-only scope and keeps all duration choices keyboard reachable', () => {
    const element = fixture.nativeElement as HTMLElement;
    expect(element.textContent).toContain('meines Mikrofons, meiner Kamera und meines Bildschirms');
    expect(element.textContent).toContain('Empfang der Medien des Gegenübers ist davon unabhängig');
    expect(element.textContent).toContain('Chat bleibt');
    expect([...element.querySelectorAll<HTMLInputElement>('input[type="radio"]')].map(input => input.value))
      .toEqual(['session', '15m', '60m']);
    expect(element.querySelector('fieldset')?.getAttribute('aria-describedby'))
      .toBe('public-pair-publication-consent-scope');
  });

  it('emits the exact selected term without requesting a media source', () => {
    const grants: unknown[] = [];
    fixture.componentInstance.grant.subscribe(term => grants.push(term));
    const radios = fixture.nativeElement.querySelectorAll('input[type="radio"]') as NodeListOf<HTMLInputElement>;
    radios[1].click();
    fixture.detectChanges();
    const button = [...fixture.nativeElement.querySelectorAll('button')]
      .find((value: HTMLButtonElement) => value.textContent?.includes('Einwilligen')) as HTMLButtonElement;
    button.click();

    expect(grants).toEqual([{ kind: 'timed', durationMs: 900_000 }]);
    expect(fixture.nativeElement.textContent).not.toContain('Browserfreigabe wird angefragt');
  });

  it('allows an unbound grant only while technical preparation is available and idle', () => {
    const grants: unknown[] = [];
    fixture.componentInstance.grant.subscribe(term => grants.push(term));
    fixture.componentRef.setInput('state', state({ status: 'unbound', binding: null }));
    fixture.componentRef.setInput('technicalPreparationAvailable', false);
    fixture.detectChanges();

    let button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.textContent).toContain('Einwilligen');
    expect(button.disabled).toBe(true);

    fixture.componentRef.setInput('technicalPreparationAvailable', true);
    fixture.detectChanges();
    button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    button.click();
    expect(grants).toEqual([{ kind: 'session' }]);

    fixture.componentRef.setInput('technicalPreparationPending', true);
    fixture.detectChanges();
    button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(fixture.nativeElement.querySelector('[role="status"]').textContent)
      .toContain('wird vorbereitet');
    expect((fixture.nativeElement.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
  });

  it.each(['inactive', 'revoked', 'expired'] as const)(
    'keeps a bound %s grant actionable independently of preparation availability',
    status => {
      fixture.componentRef.setInput('state', state({ status }));
      fixture.componentRef.setInput('technicalPreparationAvailable', false);
      fixture.detectChanges();

      expect((fixture.nativeElement.querySelector('button') as HTMLButtonElement).disabled).toBe(false);
    },
  );

  it('shows a concrete expiry and offers reversible deactivation in the same card', () => {
    const expiresAtMs = Date.UTC(2026, 7, 10, 12, 30);
    const revokes: unknown[] = [];
    fixture.componentInstance.revoke.subscribe(value => revokes.push(value));
    fixture.componentRef.setInput('state', state({
      status: 'granted', term: { kind: 'timed', durationMs: 3_600_000 }, expiresAtMs,
    }));
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    const time = element.querySelector('time');
    expect(time?.getAttribute('datetime')).toBe(new Date(expiresAtMs).toISOString());
    expect(time?.textContent?.trim()).toBeTruthy();
    const button = element.querySelector('button') as HTMLButtonElement;
    expect(button.textContent).toContain('Eigene Freigabe deaktivieren');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    button.click();
    expect(revokes).toHaveLength(1);
  });

  it('offers only an ACK-backed safe reset for a bound failure', () => {
    const revokes: unknown[] = [];
    fixture.componentInstance.revoke.subscribe(value => revokes.push(value));
    fixture.componentRef.setInput('state', state({
      status: 'failed', reasonCode: 'public_media_publication_consent_gate_failed',
    }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="alert"]').textContent)
      .toContain('ausgehende Medienpfad');
    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.textContent).toContain('Sicher zurücksetzen');
    expect(button.disabled).toBe(false);
    button.click();
    expect(revokes).toHaveLength(1);
  });

  it('never exposes reset or direct regrant for an unbound failure', () => {
    fixture.componentRef.setInput('state', state({
      status: 'failed', binding: null, reasonCode: 'public_media_publication_consent_gate_failed',
    }));
    fixture.componentRef.setInput('technicalPreparationAvailable', true);
    fixture.detectChanges();

    const button = fixture.nativeElement.querySelector('button') as HTMLButtonElement;
    expect(button.textContent).toContain('Einwilligen');
    expect(button.textContent).not.toContain('zurücksetzen');
    expect(button.disabled).toBe(true);
  });

  it('keeps expiry announced and directly regrantable while still bound', () => {
    fixture.componentRef.setInput('state', state({
      status: 'expired', reasonCode: 'public_media_publication_consent_expired',
    }));
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[role="status"]').textContent).toContain('abgelaufen');
    expect((fixture.nativeElement.querySelector('button') as HTMLButtonElement).disabled).toBe(false);
  });
});

function state(
  patch: Partial<PublicPairMediaPublicationConsentState> = {},
): PublicPairMediaPublicationConsentState {
  return {
    status: 'inactive',
    binding: {
      sessionId: 'session-a', securityEpoch: 3, contractDigest: 'a'.repeat(64), adapterGeneration: 7,
      localPeerId: 'alice', remotePeerId: 'bob', maxExpiresAtMs: Date.now() + 3_600_000,
    },
    revision: 0,
    term: null,
    slots: [],
    expiresAtMs: null,
    ...patch,
  };
}
