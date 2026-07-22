import { TestBed } from '@angular/core/testing';
import { Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { SfuBroadcastOperationsApiService } from '../../services/sfu-broadcast-operations-api.service';
import { SfuBroadcastOperatorComponent } from './sfu-broadcast-operator.component';

describe('SfuBroadcastOperatorComponent', () => {
  it('keeps reads scoped and requires explicit typed confirmation before a command', () => {
    const api = {
      read: vi.fn(() => of({
        reasonCode: 'sfu_operations_snapshot_read', snapshotRef: 'snapshot-a', items: [], nextCursor: null,
      })),
      command: vi.fn(() => of({
        ok: true, accepted: true, effectiveVersion: 2, state: 'active',
        reasonCode: 'sfu_broadcast_started', commandRef: 'command-a', replayed: false,
      })),
    };
    TestBed.configureTestingModule({ providers: [
      { provide: SfuBroadcastOperationsApiService, useValue: api },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } },
    ] });
    const fixture = TestBed.createComponent(SfuBroadcastOperatorComponent);
    const component = fixture.componentInstance;

    component.load(true);
    expect(api.read).not.toHaveBeenCalled();
    component.tenantRef = 'tenant-a';
    component.load(true);
    expect(api.read).toHaveBeenCalledOnce();

    component.commandRoomRef = 'room-a';
    component.expectedVersion = 1;
    component.prepare('start');
    component.execute();
    expect(api.command).not.toHaveBeenCalled();
    component.confirmationChecked = true;
    component.confirmationPhrase = 'FREIGEBEN';
    component.execute();
    expect(api.command).toHaveBeenCalledOnce();
    expect(component.commandRoomRef).toBe('');
    expect(component.lastCommand?.reasonCode).toBe('sfu_broadcast_started');
  });

  it('does not expose source/run evidence that is absent from the read contract', () => {
    TestBed.configureTestingModule({ providers: [
      { provide: SfuBroadcastOperationsApiService, useValue: { read: vi.fn(), command: vi.fn() } },
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
    ] });
    const component = TestBed.createComponent(SfuBroadcastOperatorComponent).componentInstance;
    expect(component.evidenceLabel({ gateState: null } as any)).toBe('not_provided');
    expect(component.evidenceLabel({ gateState: 'observe_only' } as any)).toBe('gate:observe_only');
  });

  it('reuses the prepared idempotency key after an ambiguous command failure', () => {
    const first = new Subject<any>();
    const second = new Subject<any>();
    const api = { read: vi.fn(), command: vi.fn()
      .mockReturnValueOnce(first.asObservable())
      .mockReturnValueOnce(second.asObservable()) };
    TestBed.configureTestingModule({ providers: [
      { provide: SfuBroadcastOperationsApiService, useValue: api },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } },
    ] });
    const component = TestBed.createComponent(SfuBroadcastOperatorComponent).componentInstance;
    component.commandRoomRef = 'room-a';
    component.prepare('start');
    component.confirmationChecked = true;
    component.confirmationPhrase = 'FREIGEBEN';
    component.execute();
    const firstKey = api.command.mock.calls[0][2];
    first.error(new Error('timeout'));
    component.execute();

    expect(api.command.mock.calls[1][2]).toBe(firstKey);
  });
});
