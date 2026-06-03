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
  }) as CanvasRenderingContext2D;
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
