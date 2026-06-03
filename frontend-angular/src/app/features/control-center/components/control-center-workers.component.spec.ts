import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { ControlCenterWorkersComponent } from './control-center-workers.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

class MockStateFacade {
  workers$ = of([
    { id: 'w1', runtime: 'docker', health: 'online', capabilities: ['fs', 'terminal'], boundary: 'local-only' },
  ] as any);
  loadWorkers = vi.fn();
}

describe('ControlCenterWorkersComponent', () => {
  it('maps health states to tones', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterWorkersComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterWorkersComponent);
    const cmp = fixture.componentInstance;
    expect(cmp.tone('online')).toBe('ok');
    expect(cmp.tone('degraded')).toBe('warn');
    expect(cmp.tone('offline')).toBe('danger');
  });

  it('loads workers from facade', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterWorkersComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterWorkersComponent);
    fixture.detectChanges();
    const cmp = fixture.componentInstance;

    expect(cmp.workers.length).toBeGreaterThan(0);
    expect(cmp.workers.some((w) => w.capabilities.includes('fs'))).toBe(true);
  });
});
