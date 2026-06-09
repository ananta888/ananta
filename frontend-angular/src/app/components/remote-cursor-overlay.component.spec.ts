/**
 * T10: RemoteCursorOverlayComponent
 *
 * Verifies:
 *  - mounts and renders nothing when peerCursors$ is empty
 *  - renders a labelled <div> per peer cursor
 *  - the transform is a translate3d in viewport px
 *  - the overlay hides itself when sync.cursorOverlayEnabled is false
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { PairViewSyncService, PeerCursor } from '../services/pair-view-sync.service';
import { RemoteCursorOverlayComponent } from './remote-cursor-overlay.component';

class FakeSync {
  private readonly _peerCursors$ = new Subject<ReadonlyMap<string, PeerCursor>>();
  readonly peerCursors$ = this._peerCursors$.asObservable();
  private _cursorOverlayEnabled = true;
  get cursorOverlayEnabled(): boolean { return this._cursorOverlayEnabled; }
  setCursorOverlayEnabled(enabled: boolean): void {
    if (this._cursorOverlayEnabled === enabled) return;
    this._cursorOverlayEnabled = enabled;
    this._peerCursors$.next(new Map());
  }

  emit(map: ReadonlyMap<string, PeerCursor>): void {
    this._peerCursors$.next(map);
  }
}

describe('RemoteCursorOverlayComponent (T10)', () => {
  let fake: FakeSync;

  beforeEach(() => {
    fake = new FakeSync();
    TestBed.configureTestingModule({
      imports: [RemoteCursorOverlayComponent],
      providers: [{ provide: PairViewSyncService, useValue: fake }],
    });
  });

  it('mounts without rendering cursors when peerCursors$ emits empty', () => {
    const fixture = TestBed.createComponent(RemoteCursorOverlayComponent);
    fake.emit(new Map());
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('.remote-cursor').length).toBe(0);
  });

  it('renders one cursor element per peer with the right transform', () => {
    const fixture = TestBed.createComponent(RemoteCursorOverlayComponent);
    fixture.detectChanges(); // triggers ngOnInit -> subscribes
    const map = new Map<string, PeerCursor>();
    map.set('u1', {
      userId: 'u1', userLabel: 'Alice',
      cursor: { line: null, column: null, x: 100, y: 200 },
      lastSeenAt: Date.now(),
    });
    map.set('u2', {
      userId: 'u2', userLabel: 'Bob',
      cursor: { line: null, column: null, x: 50, y: 80 },
      lastSeenAt: Date.now(),
    });
    fake.emit(map);
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;
    const cursors = root.querySelectorAll('.remote-cursor');
    expect(cursors.length).toBe(2);
    const t0 = (cursors[0] as HTMLElement).style.transform;
    const t1 = (cursors[1] as HTMLElement).style.transform;
    // We don't know which order, but both should be translate3d
    const all = [t0, t1].join('|');
    expect(all).toContain('translate3d(100px, 200px, 0)');
    expect(all).toContain('translate3d(50px, 80px, 0)');
  });

  it('falls back to line/column px approximation when x/y are absent', () => {
    const fixture = TestBed.createComponent(RemoteCursorOverlayComponent);
    fixture.detectChanges(); // triggers ngOnInit
    const map = new Map<string, PeerCursor>();
    map.set('u1', {
      userId: 'u1', userLabel: 'TC',
      cursor: { line: 3, column: 5 },
      lastSeenAt: Date.now(),
    });
    fake.emit(map);
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;
    const t = (root.querySelector('.remote-cursor') as HTMLElement).style.transform;
    // 3*12 + 4 = 40, 5*7 + 4 = 39
    expect(t).toBe('translate3d(39px, 40px, 0)');
  });

  it('hides the overlay when sync.cursorOverlayEnabled is false', () => {
    const fixture = TestBed.createComponent(RemoteCursorOverlayComponent);
    fake.emit(new Map([['u1', {
      userId: 'u1', userLabel: 'X',
      cursor: { line: null, column: null, x: 1, y: 1 },
      lastSeenAt: Date.now(),
    }]]));
    fake.setCursorOverlayEnabled(false);
    fixture.detectChanges();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('.remote-cursor').length).toBe(0);
  });
});
