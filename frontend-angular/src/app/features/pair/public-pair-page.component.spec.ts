import { TestBed } from '@angular/core/testing';

import { PublicPairPageComponent } from './public-pair-page.component';

describe('PublicPairPageComponent', () => {
  it('keeps the route as a single-runtime handoff to the embedded Pair surface', async () => {
    await TestBed.configureTestingModule({ imports: [PublicPairPageComponent] }).compileComponents();

    const fixture = TestBed.createComponent(PublicPairPageComponent);
    fixture.detectChanges();
    const host = fixture.nativeElement as HTMLElement;

    expect(host.querySelector('[data-testid="public-pair-page"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="public-pair-drawer-handoff"]')?.textContent)
      .toContain('Pair Dev läuft im AI-Snake-Fenster');
    expect(host.querySelector('app-ai-snake-share-panel')).toBeNull();
    expect(host.querySelector('app-semantic-media-program-host')).toBeNull();
  });
});
