// Private receiver for the actual packaged legacy Companion.run gate.
import fs from "node:fs/promises";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

const root = process.env.ANANTA_TEST_MEET_REPOSITORY;
const actualModel = process.env.MEET_COMPANION_INTEGRATED_REAL_LLM === "1";
const actualGpu = actualModel && process.env.MEET_COMPANION_INTEGRATED_GPU_TTS === "1";
const { machineBrowserFixture } = await import(pathToFileURL(root + "/test/helpers/machine-browser-fixture.js"));
const { installMultiPublisherObservation } = await import(pathToFileURL(root + "/test/helpers/machine-multi-publisher-observation.mjs"));
const { waitFixtureValue } = await import(pathToFileURL(root + "/test/helpers/machine-browser-wait.mjs"));
const cleanup = [];
const emit = value => process.stdout.write(JSON.stringify(value) + "\n");
async function waitForAudibleSpeech(page, peers) {
  // Respect the shared observer's 30-second per-call bound while covering
  // the owned preparation process's existing 120-second maximum.
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      await waitFixtureValue(page, peers => ({ quiet: window.__multiPublisher.quiet(),
        active: window.__multiPublisher.active(peers) }), peers, {
        timeout: 30000, accept: value => !value.quiet.failed && value.active[0],
      });
      return;
    } catch (error) {
      if (error.message !== "test_fixture_wait_deadline" || attempt === 3) throw error;
    }
  }
}
let phase = "setup";
const timer = setTimeout(() => process.stdin.destroy(), actualGpu ? 340000 : 220000);
try {
  const f = await machineBrowserFixture({ after: fn => cleanup.push(fn) }, {
    listenHost: "127.0.0.2", tlsPortProxy: true, externalMachine: true,
    lifetimeSeconds: 300, tlsConnectionLimit: 32,
    hubPublicKey: await fs.readFile(process.env.MEET_TEST_HUB_PUBLIC_KEY, "utf8"),
  });
  await f.human.evaluate(installMultiPublisherObservation);
  cleanup.push(() => f.human.evaluate(() => window.__multiPublisher.close()));
  emit({ origin: f.origin, room_id: f.roomId, certificate: f.certificatePath, network: f.testNetwork });
  let sourcePending = false;
  for await (const received of readline.createInterface({ input: process.stdin })) {
    const assigned = received.startsWith("assigned-");
    const line = assigned ? received.slice("assigned-".length) : received;
    if (line === "stop") break;
    if (line === "source-depart") {
      if (!sourcePending) throw new Error("test_source_departure_unbound");
      sourcePending = false;
      phase = "source-departure";
      await f.human.locator("#leave-room").click();
      const deadline = Date.now() + 25000;
      while (f.app.registry.members(f.roomId).some(member => member.machine)) {
        if (Date.now() >= deadline) throw new Error("test_source_stop_deadline");
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      emit({ source_departed: true, machine_departed: true });
      continue;
    }
    if (!["backlog", "hub-revoke", "pressure", "recovery", "source-revoke", "avatar-paused", "speech-paused", "speech-preparation-paused", "avatar-mid-speech", "speech-mid-speech"].includes(line)) throw new Error("test_command_invalid");
    const messageCount = line === "pressure" ? 32 : 3;
    phase = "join";
    await f.human.locator("#participant-count", { hasText: "2 / 20" }).waitFor({ timeout: 45000 });
    const machines = f.app.registry.members(f.roomId).filter(member => member.machine);
    if (machines.length !== 1) throw new Error("test_companion_member_invalid");
    const peers = [machines[0].id, machines[0].id];
    phase = "avatar";
    await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
    await waitFixtureValue(f.human, peers => window.__multiPublisher.snapshot(peers), peers, {
      timeout: 30000, accept: value => !value.failed && value.publishers[0].camera !== null,
    });
    phase = "send";
    await f.human.getByRole("button", { name: "Chat", exact: true }).click();
    await f.human.locator("#chat-form.large").waitFor();
    for (let n = 1; n <= messageCount; n++) {
      await f.human.locator("#chat-message").fill(assigned
        ? "Wie ist die Code-Architektur aufgebaut? synthetic " + n
        : "synthetic " + (line === "recovery" ? "recovery " : "backlog ") + n);
      await f.human.locator("#chat-form button").click();
    }
    phase = "answers";
    const replies = actualModel ? f.human.locator('#chat-log .chat-entry[data-machine-message="true"] > span')
      : f.human.locator("#chat-log").getByText(line === "recovery"
      ? /^Synthetic recovery answer [123]$/ : /^Synthetic companion answer (?:[1-9]|[12]\d|3[0-2])$/);
    if (["avatar-paused", "speech-paused", "speech-preparation-paused", "avatar-mid-speech", "speech-mid-speech"].includes(line)) emit({ inputs_sent: 3 });
    if (line.endsWith("-mid-speech")) {
      phase = "mid-speech";
      await replies.first().waitFor({ timeout: 150000 });
      if (actualModel && await replies.first().textContent() === "Die Antwort konnte nicht erstellt werden.") {
        throw new Error("test_actual_model_reply_failed");
      }
      await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
      // A chat reply precedes owned CPU clip preparation (bounded at 120 s).
      // Observe fresh PCM, so a past completed utterance cannot trigger pause.
      await waitForAudibleSpeech(f.human, peers);
      emit({ speech_started: true });
      await f.human.getByRole("button", { name: "Chat", exact: true }).click();
    }
    if (line === "source-revoke") {
      sourcePending = true;
      emit({ inputs_sent: 3 });
      continue;
    }
    if (line === "hub-revoke") {
      emit({ inputs_sent: 3 });
      phase = "revoked-departure";
      await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
      await f.human.locator("#participant-count", { hasText: "1 / 20" }).waitFor({ timeout: 25000 });
      if (await replies.count() !== 0) throw new Error("test_revoked_reply");
      phase = "revoked-camera";
      await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
      await waitFixtureValue(f.human, peers => window.__multiPublisher.snapshot(peers), peers, {
        timeout: 5000, accept: value => !value.failed && value.publishers[0].camera === null,
      });
      emit({ revoked: true, replies: 0, camera_absent: true });
      continue;
    }
    await replies.nth(messageCount - 1).waitFor({ timeout: actualGpu ? 300000 : 150000 });
    const texts = await replies.allTextContents();
    if (texts.length !== messageCount || texts.some(text => !text.trim() || text.length > 450)
        || (!actualModel && new Set(texts).size !== messageCount)) throw new Error("test_reply_count_invalid");
    if (line === "recovery" && await f.human.locator("#chat-log").getByText(/^Synthetic companion answer /).count() !== 0) {
      throw new Error("test_retired_input_replayed");
    }
    phase = "speech";
    await f.human.locator(".nav-item").filter({ hasText: /^Live/ }).click();
    let audioWindows = 0;
    await waitFixtureValue(f.human, peers => window.__multiPublisher.snapshot(peers), peers, {
      timeout: 15000, accept: value => {
        audioWindows = value.publishers[0].active;
        const camera = line.startsWith("avatar-") ? value.publishers[0].camera === null : value.publishers[0].camera !== null;
        return !value.failed && camera && (["speech-paused", "speech-preparation-paused"].includes(line) ? audioWindows === 0 : audioWindows > 0);
      },
    });
    emit({ replies: messageCount, unique: new Set(texts).size, captures: await f.human.evaluate(() => window.__captures),
      transform_errors: await f.human.evaluate(() => window.__transformErrors.length),
      proxy_drops: f.proxyObservation().connectionDrops, decoded_avatar: !line.startsWith("avatar-"), audio_windows: audioWindows });
  }
} catch (error) {
  const code = /^(?:test|meet)_[a-z_]{1,96}$/.test(error.message) ? error.message : "unclassified";
  emit({ error: "test_companion_live_bridge_failed", phase, timeout: error.name === "TimeoutError", code });
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  for (const close of cleanup.reverse()) await close();
}
