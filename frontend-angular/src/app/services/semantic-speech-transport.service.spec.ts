import { TestBed } from '@angular/core/testing';
import goldenCatalog from '../../../../tests/fixtures/webrtc/semantic_speech_transport_cases.json';
import { Subject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SEMANTIC_DC_VERSION, SemanticDataChannelMessage } from './webrtc-datachannel.service';
import {
  SEMANTIC_SPEECH_CRYPTO,
  SemanticSpeechCryptoPort,
  SemanticSpeechPayload,
  SemanticSpeechTransportService,
  validateSemanticSpeechPayload,
} from './semantic-speech-transport.service';
import { WebrtcSendOperation } from './webrtc-send-operation';
import { WebrtcTransportService } from './webrtc-transport.service';

const context = {
  sessionId: 'session-a', epoch: 2, localPeerId: 'alice', remotePeerId: 'bob',
  consentVersion: 3, contractDigest: 'a'.repeat(64),
};

function payload(kind: SemanticSpeechPayload['kind'] = 'transcript_revision'): SemanticSpeechPayload {
  return {
    version: 'ananta.semantic-speech.v1', kind, session_id: 'session-a', epoch: 2,
    turn_id: 'turn-a', revision: 1, sender_id: 'alice', audience_id: 'bob',
    consent_version: 3, expires_at_ms: Date.now() + 60_000, contract_digest: 'a'.repeat(64),
    source_digest: 'b'.repeat(64), authority: 'provisional', text: 'Hallo',
  };
}

describe('SemanticSpeechTransportService', () => {
  const semanticMessage$ = new Subject<SemanticDataChannelMessage>();
  const sendSemantic = vi.fn();
  const transport = {
    semanticMessage$, sendSemantic,
    enableSemanticTraffic: vi.fn(), setSemanticEpoch: vi.fn(),
  };
  let opened = new Uint8Array();
  const sealed: SemanticDataChannelMessage = {
    version: SEMANTIC_DC_VERSION, traffic_class: 'transcript', message_id: 'message-a',
    session_id: 'session-a', epoch: 2, sender_id: 'alice', audience_id: 'bob', sequence: 1,
    expires_at_ms: Date.now() + 60_000, compression: 'none',
    security: { algorithm: 'AES-GCM-256', key_id: 'key-a' }, payload_bytes: 0,
    payload_digest: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', ciphertext: '',
  };
  const cryptoPort: SemanticSpeechCryptoPort = {
    seal: vi.fn(async () => sealed),
    open: vi.fn(async () => opened),
  };
  let service: SemanticSpeechTransportService;

  beforeEach(() => {
    vi.clearAllMocks();
    opened = new Uint8Array();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechTransportService,
      { provide: WebrtcTransportService, useValue: transport },
      { provide: SEMANTIC_SPEECH_CRYPTO, useValue: cryptoPort },
    ] });
    service = TestBed.inject(SemanticSpeechTransportService);
    service.start(context);
  });

  it('prioritizes transcript over correction/features and releases bounded queue receipts', async () => {
    const pressure: number[] = [];
    service.pressure$.subscribe(value => pressure.push(value.pendingBytes));
    const operation = new WebrtcSendOperation('session-a', 2, 'c'.repeat(64), Date.now() + 10_000);
    sendSemantic.mockResolvedValue(operation);
    const sent = await service.send(payload());
    expect(cryptoPort.seal).toHaveBeenCalledOnce();
    expect(vi.mocked(cryptoPort.seal).mock.calls[0]?.[0].byteLength).toBeGreaterThan(0);
    expect(vi.mocked(cryptoPort.seal).mock.calls[0]?.[1]).toBe('transcript');
    expect(pressure.at(-1)).toBeGreaterThan(0);
    operation.acknowledge(1);
    await sent.result;
    expect(service.snapshot()).toEqual({ pendingMessages: 0, pendingBytes: 0, timers: 0 });
    expect(pressure.at(-1)).toBe(0);
  });

  it('decrypts and validates audience, epoch, consent and expiry before dispatch', async () => {
    opened = new TextEncoder().encode(JSON.stringify({ ...payload(), sender_id: 'bob', audience_id: 'alice' }));
    const received: SemanticSpeechPayload[] = [];
    service.payload$.subscribe(value => received.push(value));
    semanticMessage$.next({ ...sealed, sender_id: 'bob', audience_id: 'alice' });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(received).toHaveLength(1);
    opened = new TextEncoder().encode(JSON.stringify({ ...payload(), epoch: 1 }));
    semanticMessage$.next(sealed);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(received).toHaveLength(1);
  });

  it('can be restarted without losing the transport subscription', async () => {
    service.start(context);
    opened = new TextEncoder().encode(JSON.stringify({ ...payload(), sender_id: 'bob', audience_id: 'alice' }));
    const received: SemanticSpeechPayload[] = [];
    service.payload$.subscribe(value => received.push(value));

    semanticMessage$.next({ ...sealed, sender_id: 'bob', audience_id: 'alice' });
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(received).toHaveLength(1);
  });

  it('rejects payloads sent in the remote-to-local direction', async () => {
    await expect(service.send({ ...payload(), sender_id: 'bob', audience_id: 'alice' }))
      .rejects.toThrow('semantic_speech_send_direction_invalid');
  });

  it('invalidates an in-flight seal when the session is stopped', async () => {
    let releaseSeal: ((message: SemanticDataChannelMessage) => void) | null = null;
    vi.mocked(cryptoPort.seal).mockImplementationOnce(() => new Promise(resolve => { releaseSeal = resolve; }));
    const pending = service.send(payload());
    service.stop();
    releaseSeal?.(sealed);

    await expect(pending).rejects.toThrow('semantic_speech_operation_invalidated');
    expect(sendSemantic).not.toHaveBeenCalled();
    expect(service.snapshot()).toEqual({ pendingMessages: 0, pendingBytes: 0, timers: 0 });
  });
});

describe('semantic speech cross-runtime contract', () => {
  it('matches Python for valid, legacy, oversize, stale, tamper and unknown-field cases', () => {
    const fixtureContext = {
      sessionId: goldenCatalog.context.session_id,
      epoch: goldenCatalog.context.epoch,
      localPeerId: goldenCatalog.context.local_peer_id,
      remotePeerId: goldenCatalog.context.remote_peer_id,
      consentVersion: goldenCatalog.context.consent_version,
      contractDigest: goldenCatalog.context.contract_digest,
    };
    for (const golden of goldenCatalog.cases) {
      const candidate: Record<string, unknown> = {
        ...goldenCatalog.base,
        ...('patch' in golden ? golden.patch : {}),
      };
      if ('text_repeat' in golden && golden.text_repeat) {
        candidate['text'] = golden.text_repeat.value.repeat(golden.text_repeat.count);
      }
      if (golden.expected === 'accept') {
        expect(validateSemanticSpeechPayload(candidate, fixtureContext, goldenCatalog.now_ms).turn_id).toBe('turn-a');
      } else {
        expect(() => validateSemanticSpeechPayload(candidate, fixtureContext, goldenCatalog.now_ms), golden.name)
          .toThrow(golden.expected);
      }
    }
  });
});
