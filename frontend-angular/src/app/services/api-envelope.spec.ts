import { of } from 'rxjs';

import { isApiEnvelope, unwrapApiEnvelope, unwrapApiResponse } from './api-envelope';

describe('api envelope helpers', () => {
  it('unwraps typed backend envelopes', () => {
    const response = { status: 'success', data: { id: 'T-1' } };

    expect(isApiEnvelope(response)).toBe(true);
    expect(unwrapApiEnvelope(response)).toEqual({ id: 'T-1' });
  });

  it('passes through raw responses', () => {
    expect(isApiEnvelope([{ id: 'T-1' }])).toBe(false);
    expect(unwrapApiEnvelope([{ id: 'T-1' }])).toEqual([{ id: 'T-1' }]);
  });

  it('unwraps observables without losing the payload type', async () => {
    const value = await new Promise<{ ready: boolean }>((resolve) => {
      unwrapApiResponse(of({ status: 'success', data: { ready: true } })).subscribe(resolve);
    });

    expect(value.ready).toBe(true);
  });
});
