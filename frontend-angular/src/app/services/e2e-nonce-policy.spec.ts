import { describe, expect, it } from 'vitest';

import { E2eOutboundNoncePolicy } from './e2e-nonce-policy';

function counterRandom(): (target: Uint8Array) => Uint8Array {
  let counter = 0;
  return target => {
    counter += 1;
    new DataView(target.buffer, target.byteOffset, target.byteLength).setUint32(8, counter);
    return target;
  };
}

describe('E2eOutboundNoncePolicy', () => {
  it('keeps sealing available beyond the bounded recent collision cache', () => {
    const policy = new E2eOutboundNoncePolicy({ randomFill: counterRandom() });
    let latest = new Uint8Array();
    for (let index = 0; index < 8193; index += 1) latest = policy.nextOutbound('key\0epoch');
    expect(new DataView(latest.buffer, latest.byteOffset, latest.byteLength).getUint32(8)).toBe(8193);
  });

  it('retries recent RNG collisions and fails closed when randomness remains stuck', () => {
    const first = new Uint8Array(12).fill(7);
    const second = new Uint8Array(12).fill(8);
    const values = [first, first, second];
    let calls = 0;
    const policy = new E2eOutboundNoncePolicy({
      randomFill: target => {
        target.set(values[Math.min(calls, values.length - 1)]);
        calls += 1;
        return target;
      },
    });
    expect(policy.nextOutbound('scope')).toEqual(first);
    expect(policy.nextOutbound('scope')).toEqual(second);
    expect(calls).toBe(3);

    const stuck = new E2eOutboundNoncePolicy({ randomFill: target => { target.fill(9); return target; } });
    stuck.nextOutbound('scope');
    expect(() => stuck.nextOutbound('scope')).toThrow('nonce_generation_failed');
  });
});
