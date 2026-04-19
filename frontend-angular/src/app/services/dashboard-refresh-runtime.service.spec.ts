import { DashboardRefreshRuntimeService } from './dashboard-refresh-runtime.service';

describe('DashboardRefreshRuntimeService', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs the initial refresh and subsequent polling centrally', () => {
    const service = new DashboardRefreshRuntimeService();
    const refresh = vi.fn();

    service.start(refresh, 3000);
    expect(refresh).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(3000);
    expect(refresh).toHaveBeenCalledTimes(2);

    service.stop();
    vi.advanceTimersByTime(3000);
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
