import { chromium, expect, firefox, test } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";

const SCENARIOS = [
  "static_ui",
  "text_scroll",
  "cursor_animation",
  "camera",
  "scene_cut",
  "strong_noise",
] as const;

type VisualLifecycleResult = {
  scenario: string;
  observeOnly: boolean;
  active: boolean;
  recoveryAttempted: boolean;
  recoveryEndedInOrdinary: boolean;
  revoked: boolean;
  reconnectFenced: boolean;
  ordinaryFallback: boolean;
  authorityWasConsumedOnly: boolean;
  policyVersion: string;
};

declare global {
  interface Window {
    __ANANTA_SEMANTIC_VISUAL_E2E__?: {
      runLifecycle(scenario: string): VisualLifecycleResult;
    };
    __ANANTA_VISUAL_ORDINARY__?: {
      peer: RTCPeerConnection;
      channel: RTCDataChannel | null;
      context?: AudioContext;
      oscillator?: OscillatorNode;
      track?: MediaStreamTrack;
      audioSeen: boolean;
      controlCount: number;
    };
  }
}

test.describe("semantic visual two-browser lifecycle", () => {
  test.skip(
    process.env["RUN_SEMANTIC_MEDIA_LIVE_E2E"] !== "1",
    "real two-browser visual lifecycle evidence is required",
  );

  test("observe, active, recovery, revoke, reconnect and Ordinary fallback", async ({
    baseURL,
  }, testInfo) => {
    expect(baseURL).toBeTruthy();
    const senderBrowser = await launch(
      testInfo.project.name === "firefox" ? "firefox" : "chromium",
    );
    const receiverBrowser = await launch(
      testInfo.project.name === "firefox" ? "chromium" : "firefox",
    );
    const sender = await (await senderBrowser.newContext()).newPage();
    const receiver = await (await receiverBrowser.newContext()).newPage();
    try {
      await Promise.all([
        sender.goto(`${baseURL}/pair-view?semanticVisualLiveE2e=1`),
        receiver.goto(`${baseURL}/pair-view?semanticVisualLiveE2e=1`),
      ]);
      await expect
        .poll(() => hasDriver(sender), { timeout: 15_000 })
        .toBe(true);
      await expect
        .poll(() => hasDriver(receiver), { timeout: 15_000 })
        .toBe(true);

      for (const scenario of SCENARIOS) {
        const [senderResult, receiverResult] = await Promise.all([
          lifecycle(sender, scenario),
          lifecycle(receiver, scenario),
        ]);
        for (const result of [senderResult, receiverResult]) {
          expect(result).toMatchObject({
            scenario,
            observeOnly: true,
            active: true,
            recoveryAttempted: true,
            recoveryEndedInOrdinary: true,
            revoked: true,
            reconnectFenced: true,
            ordinaryFallback: true,
            authorityWasConsumedOnly: true,
            policyVersion: "semantic-visual-controller/1.0.0",
          });
        }
      }

      const offer = await createOrdinarySender(sender);
      const answer = await createOrdinaryReceiver(receiver, offer);
      await sender.evaluate(async (remote) => {
        const state = window.__ANANTA_VISUAL_ORDINARY__;
        if (!state) throw new Error("visual_ordinary_sender_missing");
        await state.peer.setRemoteDescription(remote);
      }, answer);
      await expect
        .poll(() => ordinaryHealthy(receiver), { timeout: 15_000 })
        .toBe(true);
      await sender.evaluate(() => {
        const channel = window.__ANANTA_VISUAL_ORDINARY__?.channel;
        if (!channel || channel.readyState !== "open")
          throw new Error("visual_control_channel_unavailable");
        channel.send(JSON.stringify({ type: "revoke", epoch: 2 }));
      });
      await expect
        .poll(() => controlCount(receiver), { timeout: 5_000 })
        .toBe(1);

      await closeOrdinary(sender);
      await closeOrdinary(receiver);
      const reconnectOffer = await createOrdinarySender(sender);
      const reconnectAnswer = await createOrdinaryReceiver(
        receiver,
        reconnectOffer,
      );
      await sender.evaluate(async (remote) => {
        const state = window.__ANANTA_VISUAL_ORDINARY__;
        if (!state) throw new Error("visual_ordinary_reconnect_sender_missing");
        await state.peer.setRemoteDescription(remote);
      }, reconnectAnswer);
      await expect
        .poll(() => ordinaryHealthy(receiver), { timeout: 15_000 })
        .toBe(true);

      testInfo.annotations.push({
        type: "semantic-visual-lifecycle-v1",
        description: JSON.stringify({
          browser_processes: 2,
          browser_engines: 2,
          scenario_count: SCENARIOS.length,
          observe_count: SCENARIOS.length * 2,
          active_count: SCENARIOS.length * 2,
          recovery_count: SCENARIOS.length * 2,
          revoke_count: SCENARIOS.length * 2,
          reconnect_count: SCENARIOS.length * 2,
          ordinary_fallback_count: SCENARIOS.length * 2,
          direct_peer_links: 2,
          ordinary_audio_receivers: 2,
        }),
      });
    } finally {
      await Promise.allSettled([
        closeOrdinary(sender),
        closeOrdinary(receiver),
      ]);
      await Promise.allSettled([
        sender.context().close(),
        receiver.context().close(),
      ]);
      await Promise.allSettled([
        senderBrowser.close(),
        receiverBrowser.close(),
      ]);
    }
  });
});

async function launch(engine: "chromium" | "firefox"): Promise<Browser> {
  return engine === "firefox"
    ? firefox.launch({ headless: true })
    : chromium.launch({ headless: true });
}

async function hasDriver(page: Page): Promise<boolean> {
  return page.evaluate(() => Boolean(window.__ANANTA_SEMANTIC_VISUAL_E2E__));
}

async function lifecycle(
  page: Page,
  scenario: string,
): Promise<VisualLifecycleResult> {
  return page.evaluate((value) => {
    const driver = window.__ANANTA_SEMANTIC_VISUAL_E2E__;
    if (!driver) throw new Error("semantic_visual_live_driver_missing");
    return driver.runLifecycle(value);
  }, scenario);
}

async function createOrdinarySender(
  page: Page,
): Promise<RTCSessionDescriptionInit> {
  return page.evaluate(async () => {
    const waitForIce = async (connection: RTCPeerConnection): Promise<void> => {
      if (connection.iceGatheringState === "complete") return;
      await new Promise<void>((resolve) => {
        const listener = () => {
          if (connection.iceGatheringState !== "complete") return;
          connection.removeEventListener("icegatheringstatechange", listener);
          resolve();
        };
        connection.addEventListener("icegatheringstatechange", listener);
      });
    };
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const destination = context.createMediaStreamDestination();
    gain.gain.value = 0.001;
    oscillator.connect(gain).connect(destination);
    oscillator.start();
    const track = destination.stream.getAudioTracks()[0];
    const peer = new RTCPeerConnection({ iceServers: [] });
    peer.addTrack(track, destination.stream);
    const channel = peer.createDataChannel("ananta-visual-lifecycle", {
      ordered: true,
    });
    window.__ANANTA_VISUAL_ORDINARY__ = {
      peer,
      channel,
      context,
      oscillator,
      track,
      audioSeen: false,
      controlCount: 0,
    };
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIce(peer);
    if (!peer.localDescription)
      throw new Error("visual_ordinary_offer_missing");
    return { type: peer.localDescription.type, sdp: peer.localDescription.sdp };
  });
}

async function createOrdinaryReceiver(
  page: Page,
  offer: RTCSessionDescriptionInit,
): Promise<RTCSessionDescriptionInit> {
  return page.evaluate(async (remote) => {
    const waitForIce = async (connection: RTCPeerConnection): Promise<void> => {
      if (connection.iceGatheringState === "complete") return;
      await new Promise<void>((resolve) => {
        const listener = () => {
          if (connection.iceGatheringState !== "complete") return;
          connection.removeEventListener("icegatheringstatechange", listener);
          resolve();
        };
        connection.addEventListener("icegatheringstatechange", listener);
      });
    };
    const peer = new RTCPeerConnection({ iceServers: [] });
    const state = {
      peer,
      channel: null as RTCDataChannel | null,
      audioSeen: false,
      controlCount: 0,
    };
    window.__ANANTA_VISUAL_ORDINARY__ = state;
    peer.ontrack = (event) => {
      if (event.track.kind === "audio") state.audioSeen = true;
      const audio = document.createElement("audio");
      audio.autoplay = true;
      audio.muted = true;
      audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      document.body.append(audio);
      void audio.play().catch(() => undefined);
    };
    peer.ondatachannel = (event) => {
      state.channel = event.channel;
      event.channel.onmessage = () => {
        state.controlCount += 1;
      };
    };
    await peer.setRemoteDescription(remote);
    await peer.setLocalDescription(await peer.createAnswer());
    await waitForIce(peer);
    if (!peer.localDescription)
      throw new Error("visual_ordinary_answer_missing");
    return { type: peer.localDescription.type, sdp: peer.localDescription.sdp };
  }, offer);
}

async function ordinaryHealthy(page: Page): Promise<boolean> {
  return page.evaluate(async () => {
    const state = window.__ANANTA_VISUAL_ORDINARY__;
    if (!state || !state.audioSeen) return false;
    const report = await state.peer.getStats();
    let bytes = 0;
    report.forEach((row) => {
      if (row.type === "inbound-rtp" && row.kind === "audio")
        bytes += Number(row.bytesReceived ?? 0);
    });
    return bytes > 0;
  });
}

async function controlCount(page: Page): Promise<number> {
  return page.evaluate(
    () => window.__ANANTA_VISUAL_ORDINARY__?.controlCount ?? 0,
  );
}

async function closeOrdinary(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const state = window.__ANANTA_VISUAL_ORDINARY__;
    if (!state) return;
    state.channel?.close();
    state.peer.getSenders().forEach((sender) => sender.track?.stop());
    state.peer.getReceivers().forEach((receiver) => receiver.track?.stop());
    state.peer.close();
    state.oscillator?.stop();
    await state.context?.close();
    delete window.__ANANTA_VISUAL_ORDINARY__;
  });
}
