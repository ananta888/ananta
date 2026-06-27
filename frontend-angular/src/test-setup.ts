import '@angular/compiler';
import { getTestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from '@angular/platform-browser-dynamic/testing';

// JSDOM in Node does not always provide WebCrypto. Some auth code relies on crypto.subtle.
// Keep this minimal and Node-native for unit tests.
const g: any = globalThis as any;
if (!g.crypto || !g.crypto.subtle) {
  const nodeCrypto = require('node:crypto');
  g.crypto = nodeCrypto.webcrypto;
}

// JSDOM provides localStorage, but vitest's per-file module resets can
// tear it down between test files. The auth services use localStorage
// from field initializers and async cleanup paths, so a ReferenceError
// here manifests as a vitest "unhandled error" that fails the run.
// Polyfill with a no-op in-memory stub when localStorage is missing.
if (typeof (globalThis as any).localStorage === 'undefined') {
  const memory = new Map<string, string>();
  const stub = {
    getItem: (k: string) => (memory.has(k) ? memory.get(k)! : null),
    setItem: (k: string, v: string) => { memory.set(k, String(v)); },
    removeItem: (k: string) => { memory.delete(k); },
    clear: () => { memory.clear(); },
    key: (i: number) => Array.from(memory.keys())[i] ?? null,
    get length() { return memory.size; },
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: stub,
    writable: false,
    configurable: true,
  });
}

const createCanvas2dContextStub = () => {
  const state: Record<string, unknown> = {};
  return new Proxy(state, {
    get(target, prop) {
      if (prop === 'measureText') {
        return (text: string) => ({ width: String(text).length * 8 } as TextMetrics);
      }
      if (prop === 'canvas') {
        return target.canvas ?? null;
      }
      if (prop === 'createLinearGradient') {
        return () => ({ addColorStop() {} });
      }
      if (prop === 'createPattern') {
        return () => null;
      }
      if (prop === 'getContextAttributes') {
        return () => ({});
      }
      return typeof prop === 'string' && prop in target ? target[prop] : () => {};
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
};

const canvasProto = (globalThis as any).HTMLCanvasElement?.prototype;
if (canvasProto && !canvasProto.__anantaPatchedGetContext) {
  const originalGetContext = canvasProto.getContext?.bind(canvasProto);
  Object.defineProperty(canvasProto, '__anantaPatchedGetContext', {
    value: true,
    configurable: true,
  });
  canvasProto.getContext = function getContext(type: string, ...args: unknown[]) {
    if (type === '2d') {
      return createCanvas2dContextStub();
    }
    if (type === 'webgl' || type === 'experimental-webgl') {
      return null;
    }
    return originalGetContext ? originalGetContext(type, ...args) : null;
  };
}

getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting(),
);
