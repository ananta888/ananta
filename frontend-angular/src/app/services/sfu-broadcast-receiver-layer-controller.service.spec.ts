import { describe, expect, it, vi } from 'vitest';

import {
  SfuBroadcastReceiverLayerControllerService,
  type SfuReceiverLayerControllerEnvironment,
} from './sfu-broadcast-receiver-layer-controller.service';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';
import type { ValidatedLayerProjection } from './sfu-layer-projection.validator';

function projection(expiresAt = '2027-01-15T08:00:10Z'): ValidatedLayerProjection {
  return {
    brand: 'validated-layer-projection', kind: 'receiver', scopeKey: 'receiver',
    version: 1, fencingToken: 1,
    contract: {
      contractId: 'ananta.sfu-receiver-layer-projection.v1',
      document: {
        subscription_ref: 'sub-1', publication_ref: 'pub-1',
        expires_at: expiresAt, resolution: 'applied', safe_outcome: 'apply_projection',
        corridor: { minimum_layer: { spatial_id: 0, temporal_id: 0 }, maximum_layer: { spatial_id: 2, temporal_id: 2 } },
        effective_layer: { spatial_id: 0, temporal_id: 0 },
      },
    },
  } as unknown as ValidatedLayerProjection;
}

function environment(): SfuReceiverLayerControllerEnvironment & {
  now: number;
  runNext(maxDelayMs: number): void;
  pendingTimerCount(): number;
} {
  let order = 0;
  const timers: Array<Readonly<{
    id: number; callback: () => void; delayMs: number; order: number;
  }>> = [];
  const value = {
    now: Date.parse('2027-01-15T08:00:00Z'),
    nowMs: () => value.now,
    setTimer: (callback: () => void, delayMs: number) => {
      const id = ++order;
      timers.push({ id, callback, delayMs, order });
      return id as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimer: (timer: ReturnType<typeof setTimeout>) => {
      const index = timers.findIndex(item => item.id === Number(timer));
      if (index >= 0) timers.splice(index, 1);
    },
    runNext: (maxDelayMs: number) => {
      timers.sort((left, right) => left.delayMs - right.delayMs || left.order - right.order);
      const index = timers.findIndex(item => item.delayMs <= maxDelayMs);
      if (index < 0) throw new Error('eligible fake timer unavailable');
      const [timer] = timers.splice(index, 1);
      timer.callback();
    },
    pendingTimerCount: () => timers.length,
  };
  return value;
}

describe('SfuBroadcastReceiverLayerControllerService', () => {
  it('is the sole bounded manual-quality writer and applies hysteresis', async () => {
    const env = environment();
    const setter = vi.fn(async () => undefined);
    const port: SfuSubscriptionLayerPort = {
      layerControlMode: 'manual_quality', applyRemoteSubscriptions: () => undefined,
      setRemotePublicationSubscribed: () => undefined, setRemotePublicationQuality: setter,
    };
    const controller = new SfuBroadcastReceiverLayerControllerService(env);
    controller.bind({ subscriptionRef: 'sub-1', publicationRef: 'pub-1', port, projection: projection() });
    await Promise.resolve();
    expect(setter).toHaveBeenCalledWith('pub-1', 'low');
    env.now += 6000;
    for (let index = 0; index < 3; index += 1) {
      controller.observe({ subscriptionRef: 'sub-1', qualityBasisPoints: 800, observedAtMs: env.now });
    }
    await Promise.resolve();
    expect(setter).toHaveBeenLastCalledWith('pub-1', 'high');
  });

  it('never writes manual quality in adaptive-stream mode', async () => {
    const env = environment();
    const setter = vi.fn(async () => undefined);
    const controller = new SfuBroadcastReceiverLayerControllerService(env);
    controller.bind({
      subscriptionRef: 'sub-1', publicationRef: 'pub-1', projection: projection(),
      port: {
        layerControlMode: 'adaptive_stream', applyRemoteSubscriptions: () => undefined,
        setRemotePublicationSubscribed: () => undefined, setRemotePublicationQuality: setter,
      },
    });
    await Promise.resolve();
    expect(setter).not.toHaveBeenCalled();
  });

  it('bounds retries and cancels timers on stop', async () => {
    const env = environment();
    const setter = vi.fn(async () => { throw new Error('temporary'); });
    const controller = new SfuBroadcastReceiverLayerControllerService(env);
    controller.bind({
      subscriptionRef: 'sub-1', publicationRef: 'pub-1', projection: projection(),
      port: {
        layerControlMode: 'manual_quality', applyRemoteSubscriptions: () => undefined,
        setRemotePublicationSubscribed: () => undefined, setRemotePublicationQuality: setter,
      },
    });
    await Promise.resolve();
    env.runNext(250); await Promise.resolve();
    env.runNext(500); await Promise.resolve();
    expect(setter).toHaveBeenCalledTimes(3);
    controller.stop('sub-1');
    expect(env.pendingTimerCount()).toBe(0);
  });

  it('keeps the healthy binding when a replacement projection is invalid', async () => {
    const env = environment();
    const setter = vi.fn(async () => undefined);
    const controller = new SfuBroadcastReceiverLayerControllerService(env);
    const port: SfuSubscriptionLayerPort = {
      layerControlMode: 'manual_quality', applyRemoteSubscriptions: () => undefined,
      setRemotePublicationSubscribed: () => undefined, setRemotePublicationQuality: setter,
    };
    controller.bind({ subscriptionRef: 'sub-1', publicationRef: 'pub-1', port, projection: projection() });
    await Promise.resolve();
    const invalid = {
      ...projection(),
      contract: {
        ...projection().contract,
        document: { ...projection().contract.document, publication_ref: 'pub-other' },
      },
    } as unknown as ValidatedLayerProjection;

    expect(() => controller.bind({
      subscriptionRef: 'sub-1', publicationRef: 'pub-1', port, projection: invalid,
    })).toThrow('sfu_receiver_projection_scope_mismatch');
    env.now += 6000;
    for (let index = 0; index < 3; index += 1) {
      controller.observe({ subscriptionRef: 'sub-1', qualityBasisPoints: 800, observedAtMs: env.now });
    }
    await Promise.resolve();
    expect(setter).toHaveBeenLastCalledWith('pub-1', 'high');
  });

  it('fences an in-flight upgrade when the projection expires', async () => {
    const env = environment();
    const high = deferred<void>();
    const setter = vi.fn(async (_publicationRef: string, next: string) => {
      if (next === 'high') await high.promise;
    });
    const controller = new SfuBroadcastReceiverLayerControllerService(env);
    controller.bind({
      subscriptionRef: 'sub-1', publicationRef: 'pub-1', projection: projection(),
      port: {
        layerControlMode: 'manual_quality', applyRemoteSubscriptions: () => undefined,
        setRemotePublicationSubscribed: () => undefined, setRemotePublicationQuality: setter,
      },
    });
    await Promise.resolve();
    env.now += 6000;
    for (let index = 0; index < 3; index += 1) {
      controller.observe({ subscriptionRef: 'sub-1', qualityBasisPoints: 800, observedAtMs: env.now });
    }
    await Promise.resolve();
    env.now = Date.parse('2027-01-15T08:00:10Z');
    env.runNext(10_000);
    await Promise.resolve();
    high.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(setter).toHaveBeenLastCalledWith('pub-1', 'low');
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>(next => { resolve = next; });
  return { promise, resolve };
}
