import { TestBed } from '@angular/core/testing';

import { AgentApiTransport } from './agent-api-transport.service';
import { SystemApiClient } from './system-api.client';

describe('SystemApiClient', () => {
  it('allows the backend readiness probe to reach its documented deadline', () => {
    TestBed.configureTestingModule({
      providers: [
        SystemApiClient,
        { provide: AgentApiTransport, useValue: {} },
      ],
    });

    const client = TestBed.inject(SystemApiClient);

    expect(client.readinessTimeoutMs).toBeGreaterThan(10_000);
  });
});
