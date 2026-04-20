import '@angular/compiler';
import { getTestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from '@angular/platform-browser-dynamic/testing';

// JSDOM in Node does not always provide WebCrypto. Some auth code relies on crypto.subtle.
// Keep this minimal and Node-native for unit tests.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const g: any = globalThis as any;
if (!g.crypto || !g.crypto.subtle) {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const nodeCrypto = require('node:crypto');
  g.crypto = nodeCrypto.webcrypto;
}

getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting(),
);
