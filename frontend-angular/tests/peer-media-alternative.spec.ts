import { BrowserType, Page, chromium, expect, firefox, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

type Decision = 'bounded_experiment' | 'no_go';

interface AlternativeMediaMeasurement {
  readonly engine: string;
  readonly browserVersion: string;
  readonly decision: Decision;
  readonly reasonCodes: readonly string[];
  readonly compatibility: Readonly<{
    videoEncoder: boolean;
    videoDecoder: boolean;
    audioEncoder: boolean;
    audioDecoder: boolean;
    vp8: boolean;
    opus: boolean;
  }>;
  readonly transport: Readonly<{
    kind: 'rtc_data_channel';
    ordered: false;
    maxRetransmits: 0;
    protocol: string;
  }>;
  readonly metrics: Readonly<{
    durationMs: number;
    producedVideoFrames: number;
    sentVideoFrames: number;
    decodedVideoFrames: number;
    producedAudioFrames: number;
    sentAudioFrames: number;
    decodedAudioFrames: number;
    injectedVideoDrops: number;
    transportLossRatio: number;
    decodeLossRatio: number;
    p95TransportLatencyMs: number;
    avSyncErrorMs: number;
    keyframeRecovered: boolean;
    keyframeRecoveryMs: number | null;
    maximumBufferedAmount: number;
  }>;
  readonly humanInterventionRequired: false;
}

const ENGINES: ReadonlyArray<readonly [string, BrowserType]> = [
  ['chromium', chromium],
  ['firefox', firefox],
];

test.describe.configure({ mode: 'serial' });

for (const [engine, browserType] of ENGINES) {
  test(`${engine} evaluates WebCodecs over an unordered unreliable DataChannel`, async () => {
    test.skip(
      !process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'],
      'requires a Hub-reserved synthetic test evidence assignment',
    );
    const assignment = JSON.parse(process.env['ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON'] ?? '{}') as {
      run_id?: string; task_id?: string; evidence_scope?: string;
    };
    expect(assignment.run_id).toMatch(/^RUN_/);
    expect(assignment.task_id).toBe('DPM-POC-003');
    expect(assignment.evidence_scope).toBe('test');
    const browser = await browserType.launch({ headless: true });
    try {
      const page = await browser.newPage();
      await page.goto(process.env['ANANTA_PEER_MEDIA_ORIGIN'] ?? 'http://127.0.0.1:41739');
      const measurement = await evaluateAlternative(page, engine, browser.version());
      expect(measurement.transport).toEqual({
        kind: 'rtc_data_channel', ordered: false, maxRetransmits: 0, protocol: '',
      });
      expect(measurement.humanInterventionRequired).toBe(false);
      if (measurement.decision === 'bounded_experiment') {
        expect(Object.values(measurement.compatibility).every(Boolean)).toBe(true);
        expect(measurement.metrics.decodedVideoFrames).toBeGreaterThan(0);
        expect(measurement.metrics.decodedAudioFrames).toBeGreaterThan(0);
        expect(measurement.metrics.avSyncErrorMs).toBeLessThanOrEqual(40);
        expect(measurement.metrics.p95TransportLatencyMs).toBeLessThan(250);
        expect(measurement.metrics.keyframeRecovered).toBe(true);
        expect(measurement.reasonCodes).toContain('application_media_congestion_control_unverified');
      } else {
        expect(measurement.reasonCodes.some(code => code.endsWith('_unavailable'))).toBe(true);
      }
      const outputDirectory = process.env['ANANTA_PEER_MEDIA_ALTERNATIVE_DIR']
        ?? '/tmp/ananta-peer-media-alternative';
      await mkdir(outputDirectory, { recursive: true });
      await writeFile(
        `${outputDirectory}/${engine}.json`,
        `${JSON.stringify(measurement, null, 2)}\n`,
        { encoding: 'utf8' },
      );
    } finally {
      await browser.close();
    }
  });
}

async function evaluateAlternative(
  page: Page,
  engine: string,
  browserVersion: string,
): Promise<AlternativeMediaMeasurement> {
  return page.evaluate(async ({ engine: browserEngine, browserVersion: version }) => {
    const capabilities = {
      videoEncoder: typeof VideoEncoder === 'function',
      videoDecoder: typeof VideoDecoder === 'function',
      audioEncoder: typeof AudioEncoder === 'function',
      audioDecoder: typeof AudioDecoder === 'function',
      vp8: false,
      opus: false,
    };
    const base = {
      engine: browserEngine,
      browserVersion: version,
      compatibility: capabilities,
      transport: {
        kind: 'rtc_data_channel' as const,
        ordered: false as const,
        maxRetransmits: 0 as const,
        protocol: '',
      },
      humanInterventionRequired: false as const,
    };
    const emptyMetrics = {
      durationMs: 0,
      producedVideoFrames: 0,
      sentVideoFrames: 0,
      decodedVideoFrames: 0,
      producedAudioFrames: 0,
      sentAudioFrames: 0,
      decodedAudioFrames: 0,
      injectedVideoDrops: 0,
      transportLossRatio: 0,
      decodeLossRatio: 0,
      p95TransportLatencyMs: 0,
      avSyncErrorMs: 0,
      keyframeRecovered: false,
      keyframeRecoveryMs: null,
      maximumBufferedAmount: 0,
    };
    const unavailable = Object.entries(capabilities)
      .filter(([name, available]) => name !== 'vp8' && name !== 'opus' && !available)
      .map(([name]) => `${name.replace(/[A-Z]/g, value => `_${value.toLowerCase()}`)}_unavailable`);
    if (unavailable.length > 0) {
      return { ...base, decision: 'no_go' as const, reasonCodes: unavailable, metrics: emptyMetrics };
    }
    const videoConfig: VideoEncoderConfig = {
      codec: 'vp8', width: 320, height: 180, bitrate: 240_000, framerate: 30,
    };
    const audioConfig: AudioEncoderConfig = {
      codec: 'opus', sampleRate: 48_000, numberOfChannels: 1, bitrate: 64_000,
    };
    capabilities.vp8 = (await VideoEncoder.isConfigSupported(videoConfig)).supported === true
      && (await VideoDecoder.isConfigSupported(videoConfig)).supported === true;
    capabilities.opus = (await AudioEncoder.isConfigSupported(audioConfig)).supported === true
      && (await AudioDecoder.isConfigSupported(audioConfig)).supported === true;
    const codecReasons = [
      ...(capabilities.vp8 ? [] : ['vp8_unavailable']),
      ...(capabilities.opus ? [] : ['opus_unavailable']),
    ];
    if (codecReasons.length > 0) {
      return { ...base, decision: 'no_go' as const, reasonCodes: codecReasons, metrics: emptyMetrics };
    }

    const offerer = new RTCPeerConnection();
    const answerer = new RTCPeerConnection();
    const sender = offerer.createDataChannel('bounded-media', { ordered: false, maxRetransmits: 0 });
    sender.binaryType = 'arraybuffer';
    const receiverPromise = new Promise<RTCDataChannel>(resolve => {
      answerer.ondatachannel = event => resolve(event.channel);
    });
    await offerer.setLocalDescription(await offerer.createOffer());
    await waitForIce(offerer);
    await answerer.setRemoteDescription(offerer.localDescription!);
    await answerer.setLocalDescription(await answerer.createAnswer());
    await waitForIce(answerer);
    await offerer.setRemoteDescription(answerer.localDescription!);
    const receiver = await receiverPromise;
    receiver.binaryType = 'arraybuffer';
    await Promise.all([waitForOpen(sender), waitForOpen(receiver)]);

    const startedAt = performance.now();
    const transportLatencies: number[] = [];
    let producedVideoFrames = 0;
    let sentVideoFrames = 0;
    let decodedVideoFrames = 0;
    let producedAudioFrames = 0;
    let sentAudioFrames = 0;
    let decodedAudioFrames = 0;
    let receivedFrames = 0;
    let injectedVideoDrops = 0;
    let lastVideoTimestamp = 0;
    let lastAudioTimestamp = 0;
    let recoverySentAt: number | null = null;
    let keyframeRecoveryMs: number | null = null;
    let maximumBufferedAmount = 0;
    const decoderErrors: string[] = [];
    const videoDecoder = new VideoDecoder({
      output: frame => {
        decodedVideoFrames += 1;
        lastVideoTimestamp = frame.timestamp;
        if (frame.timestamp >= 499_995 && recoverySentAt !== null && keyframeRecoveryMs === null) {
          keyframeRecoveryMs = performance.now() - recoverySentAt;
        }
        frame.close();
      },
      error: error => decoderErrors.push(String(error)),
    });
    const audioDecoder = new AudioDecoder({
      output: frame => {
        decodedAudioFrames += 1;
        lastAudioTimestamp = frame.timestamp;
        frame.close();
      },
      error: error => decoderErrors.push(String(error)),
    });
    videoDecoder.configure({ codec: 'vp8', codedWidth: 320, codedHeight: 180 });
    audioDecoder.configure({ codec: 'opus', sampleRate: 48_000, numberOfChannels: 1 });
    receiver.onmessage = event => {
      if (!(event.data instanceof ArrayBuffer) || event.data.byteLength < 18) return;
      const view = new DataView(event.data);
      const kind = view.getUint8(0);
      const key = view.getUint8(1) === 1;
      const timestamp = view.getFloat64(2);
      const sentAt = view.getFloat64(10);
      const payload = event.data.slice(18);
      receivedFrames += 1;
      transportLatencies.push(performance.now() - sentAt);
      if (kind === 0) {
        videoDecoder.decode(new EncodedVideoChunk({ type: key ? 'key' : 'delta', timestamp, data: payload }));
      } else {
        audioDecoder.decode(new EncodedAudioChunk({ type: 'key', timestamp, data: payload }));
      }
    };
    const sendChunk = (kind: 0 | 1, chunk: EncodedVideoChunk | EncodedAudioChunk): void => {
      if (kind === 0 && chunk.type === 'delta' && chunk.timestamp >= 300_000 && chunk.timestamp < 499_995) {
        injectedVideoDrops += 1;
        return;
      }
      const payload = new Uint8Array(chunk.byteLength);
      chunk.copyTo(payload);
      const packet = new ArrayBuffer(18 + payload.byteLength);
      const view = new DataView(packet);
      view.setUint8(0, kind);
      view.setUint8(1, chunk.type === 'key' ? 1 : 0);
      view.setFloat64(2, chunk.timestamp);
      view.setFloat64(10, performance.now());
      new Uint8Array(packet, 18).set(payload);
      maximumBufferedAmount = Math.max(maximumBufferedAmount, sender.bufferedAmount);
      if (kind === 0) {
        sentVideoFrames += 1;
        if (chunk.type === 'key' && chunk.timestamp >= 499_995) recoverySentAt = performance.now();
      } else {
        sentAudioFrames += 1;
      }
      sender.send(packet);
    };
    const videoEncoder = new VideoEncoder({
      output: chunk => {
        producedVideoFrames += 1;
        sendChunk(0, chunk);
      },
      error: error => decoderErrors.push(String(error)),
    });
    const audioEncoder = new AudioEncoder({
      output: chunk => {
        producedAudioFrames += 1;
        sendChunk(1, chunk);
      },
      error: error => decoderErrors.push(String(error)),
    });
    videoEncoder.configure(videoConfig);
    audioEncoder.configure(audioConfig);
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 180;
    const context = canvas.getContext('2d')!;
    for (let index = 0; index < 30; index += 1) {
      context.fillStyle = `hsl(${index * 12} 75% 45%)`;
      context.fillRect(0, 0, canvas.width, canvas.height);
      const frame = new VideoFrame(canvas, { timestamp: index * 33_333, duration: 33_333 });
      videoEncoder.encode(frame, { keyFrame: index % 15 === 0 });
      frame.close();
    }
    for (let index = 0; index < 50; index += 1) {
      const samples = new Float32Array(960);
      for (let sample = 0; sample < samples.length; sample += 1) {
        samples[sample] = Math.sin((index * samples.length + sample) * 2 * Math.PI * 440 / 48_000) * 0.1;
      }
      const audio = new AudioData({
        format: 'f32-planar', sampleRate: 48_000, numberOfFrames: 960,
        numberOfChannels: 1, timestamp: index * 20_000, data: samples,
      });
      audioEncoder.encode(audio);
      audio.close();
    }
    await Promise.all([videoEncoder.flush(), audioEncoder.flush()]);
    const expectedReceipts = sentVideoFrames + sentAudioFrames;
    const deadline = performance.now() + 2_000;
    while (receivedFrames < expectedReceipts && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    await Promise.allSettled([videoDecoder.flush(), audioDecoder.flush()]);
    const durationMs = performance.now() - startedAt;
    const sortedLatencies = [...transportLatencies].sort((left, right) => left - right);
    const percentileIndex = Math.max(0, Math.ceil(sortedLatencies.length * 0.95) - 1);
    const measurement = {
      ...base,
      decision: 'bounded_experiment' as const,
      reasonCodes: [
        'application_media_congestion_control_unverified',
        'renderer_parity_unverified',
        ...(decoderErrors.length > 0 ? ['decoder_reported_recoverable_errors'] : []),
      ],
      metrics: {
        durationMs,
        producedVideoFrames,
        sentVideoFrames,
        decodedVideoFrames,
        producedAudioFrames,
        sentAudioFrames,
        decodedAudioFrames,
        injectedVideoDrops,
        transportLossRatio: expectedReceipts === 0 ? 1 : (expectedReceipts - receivedFrames) / expectedReceipts,
        decodeLossRatio: sentVideoFrames === 0 ? 1 : (sentVideoFrames - decodedVideoFrames) / sentVideoFrames,
        p95TransportLatencyMs: sortedLatencies[percentileIndex] ?? 0,
        avSyncErrorMs: Math.abs(lastAudioTimestamp - lastVideoTimestamp) / 1_000,
        keyframeRecovered: keyframeRecoveryMs !== null,
        keyframeRecoveryMs,
        maximumBufferedAmount,
      },
    };
    videoEncoder.close();
    audioEncoder.close();
    videoDecoder.close();
    audioDecoder.close();
    sender.close();
    offerer.close();
    answerer.close();
    return measurement;

    async function waitForIce(peer: RTCPeerConnection): Promise<void> {
      if (peer.iceGatheringState === 'complete') return;
      await new Promise<void>(resolve => {
        peer.addEventListener('icegatheringstatechange', () => {
          if (peer.iceGatheringState === 'complete') resolve();
        });
      });
    }

    async function waitForOpen(channel: RTCDataChannel): Promise<void> {
      if (channel.readyState === 'open') return;
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('data_channel_open_timeout')), 5_000);
        channel.addEventListener('open', () => {
          clearTimeout(timer);
          resolve();
        }, { once: true });
      });
    }
  }, { engine, browserVersion });
}
