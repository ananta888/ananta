import { ControlCenterWorkersComponent } from './control-center-workers.component';

describe('ControlCenterWorkersComponent', () => {
  it('maps health states to tones', () => {
    const cmp = new ControlCenterWorkersComponent();
    expect(cmp.tone('online')).toBe('ok');
    expect(cmp.tone('degraded')).toBe('warn');
    expect(cmp.tone('offline')).toBe('danger');
  });

  it('contains capability matrix rows', () => {
    const cmp = new ControlCenterWorkersComponent();
    expect(cmp.workers.length).toBeGreaterThan(0);
    expect(cmp.workers.some(w => w.capabilities.includes('fs'))).toBe(true);
  });
});
