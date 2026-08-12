import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { PairRemoteSnakeOverlayComponent, pairCursorColor } from './pair-remote-snake-overlay.component';
import { PairViewSyncService } from '../services/pair-view-sync.service';
import { ShareSessionService } from '../services/share-session.service';

describe('PairRemoteSnakeOverlayComponent', () => {
  it('has deterministic participant colors and only consumes compact Pair projections', () => {
    const peerCursors$ = new Subject<ReadonlyMap<string, any>>();
    const publicPairRuntimeState$ = new BehaviorSubject<any>('public');
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
    TestBed.configureTestingModule({
      imports: [PairRemoteSnakeOverlayComponent],
      providers: [
        { provide: PairViewSyncService, useValue: { peerCursors$ } },
        { provide: ShareSessionService, useValue: { publicPairRuntimeState$ } },
      ],
    });
    const fixture = TestBed.createComponent(PairRemoteSnakeOverlayComponent);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('[data-testid="pair-remote-snake-overlay"]')).not.toBeNull();
    expect(pairCursorColor('peer-a')).toBe(pairCursorColor('peer-a'));
    expect(pairCursorColor('peer-a')).not.toBe(pairCursorColor('peer-b'));
  });
});
