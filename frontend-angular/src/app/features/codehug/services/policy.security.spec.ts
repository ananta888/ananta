import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of } from 'rxjs';

import { PolicyService } from './policy.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';

function mockHub() {
  return {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of({})),
    put: vi.fn(() => of({})),
  };
}

function mockDir() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] };
}

function setup() {
  const hub = mockHub();
  TestBed.configureTestingModule({
    providers: [
      PolicyService,
      { provide: HubApiCoreService, useValue: hub },
      { provide: AgentDirectoryService, useValue: mockDir() },
    ],
  });
  return { svc: TestBed.inject(PolicyService), hub };
}

describe('PolicyService — Audit + Risk + RateLimit', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('appendAudit adds entry and respects limit', () => {
    const { svc } = setup();
    for (let i = 0; i < 5; i++) {
      svc.appendAudit({ kind: 'tool-call', action: `act-${i}` });
    }
    expect(svc.auditLog().length).toBe(5);
    expect(svc.auditLog()[0].action).toBe('act-4');
  });

  it('clearAudit empties the list', () => {
    const { svc } = setup();
    svc.appendAudit({ kind: 'tool-call', action: 'x' });
    svc.clearAudit();
    expect(svc.auditLog().length).toBe(0);
  });

  it('assessToolRisk: low for harmless tool', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('help', { topic: 'greeting' });
    expect(r.level).toBe('low');
    expect(r.recommendation).toBe('allow');
  });

  it('assessToolRisk: high for write_file', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('write_file', { path: '/x.py' });
    expect(r.level).toBe('high');
    expect(r.recommendation).toBe('require_approval');
  });

  it('assessToolRisk: medium for read_file', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('read_file', { path: '/x.py' });
    expect(r.level).toBe('medium');
    expect(r.recommendation).toBe('warn');
  });

  it('assessToolRisk: critical for destructive pattern', () => {
    const { svc } = setup();
    const r = svc.assessToolRisk('shell_exec', { cmd: 'rm -rf /' });
    expect(r.level).toBe('critical');
    expect(r.recommendation).toBe('deny');
  });

  it('checkRate: counts requests and enforces limit', () => {
    const { svc } = setup();
    const r1 = svc.checkRate('k1', 2);
    expect(r1.allowed).toBe(true);
    svc.checkRate('k1', 2);
    const r3 = svc.checkRate('k1', 2);
    expect(r3.allowed).toBe(false);
    expect(r3.remaining).toBe(0);
  });

  it('checkRate: separate buckets are independent', () => {
    const { svc } = setup();
    svc.checkRate('k1', 1);
    expect(svc.checkRate('k1', 1).allowed).toBe(false);
    expect(svc.checkRate('k2', 1).allowed).toBe(true);
  });

  it('resetRate: removes bucket', () => {
    const { svc } = setup();
    svc.checkRate('k1', 1);
    svc.checkRate('k1', 1);
    expect(svc.checkRate('k1', 1).allowed).toBe(false);
    svc.resetRate('k1');
    expect(svc.checkRate('k1', 1).allowed).toBe(true);
  });
});