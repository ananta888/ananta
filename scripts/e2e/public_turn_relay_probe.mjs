#!/usr/bin/env node
/** Exercise public UDP, TCP and TLS TURN relays with real browser peers. */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(resolve(root, "frontend-angular/package.json"));
const { chromium, firefox } = require("playwright");

const ENGINES = { chromium, firefox };
const VARIANTS = {
  udp: (host) => `turn:${host}:3478?transport=udp`,
  tcp: (host) => `turn:${host}:3478?transport=tcp`,
  tls: (host) => `turns:${host}:5349?transport=tcp`,
};

class PublicTurnProbeError extends Error {
  constructor(reasonCode) {
    super(reasonCode);
    this.name = "PublicTurnProbeError";
  }
}

function parseArguments(argv) {
  const values = {
    output: "/tmp/ananta-public-turn-relay.json",
    host: "webrtc.ananta.de",
    timeoutMs: 30000,
  };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!value || !["--output", "--host", "--timeout-ms"].includes(key)) {
      throw new PublicTurnProbeError("public_turn_probe_arguments_invalid");
    }
    if (key === "--output") values.output = value;
    if (key === "--host") values.host = value;
    if (key === "--timeout-ms") values.timeoutMs = Number(value);
  }
  if (
    !/^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$/u.test(values.host) ||
    !Number.isInteger(values.timeoutMs) ||
    values.timeoutMs < 5000 ||
    values.timeoutMs > 120000
  ) {
    throw new PublicTurnProbeError("public_turn_probe_arguments_invalid");
  }
  return values;
}

async function readSecret() {
  if (process.stdin.isTTY) {
    throw new PublicTurnProbeError("public_turn_probe_secret_stdin_required");
  }
  const chunks = [];
  let length = 0;
  for await (const chunk of process.stdin) {
    length += chunk.length;
    if (length > 4096) {
      throw new PublicTurnProbeError("public_turn_probe_secret_invalid");
    }
    chunks.push(chunk);
  }
  const secret = Buffer.concat(chunks).toString("utf8").trim();
  if (!secret || /\s/u.test(secret)) {
    throw new PublicTurnProbeError("public_turn_probe_secret_invalid");
  }
  return secret;
}

function temporaryCredential(secret) {
  const username = `${Math.floor(Date.now() / 1000) + 600}:ananta-public-evidence`;
  const credential = crypto
    .createHmac("sha1", secret)
    .update(username)
    .digest("base64");
  return { username, credential };
}

async function probeVariant(page, configuration) {
  return page.evaluate(async ({ iceUrl, username, credential, timeoutMs }) => {
    const waitFor = (condition, reasonCode) =>
      new Promise((resolve, reject) => {
        const started = Date.now();
        const poll = () => {
          if (condition()) {
            resolve();
          } else if (Date.now() - started >= timeoutMs) {
            reject(new Error(reasonCode));
          } else {
            setTimeout(poll, 25);
          }
        };
        poll();
      });
    const waitForGathering = (peer) =>
      peer.iceGatheringState === "complete"
        ? Promise.resolve()
        : new Promise((resolve, reject) => {
            const timer = setTimeout(
              () => reject(new Error("ice_gathering_timeout")),
              timeoutMs,
            );
            peer.addEventListener("icegatheringstatechange", () => {
              if (peer.iceGatheringState === "complete") {
                clearTimeout(timer);
                resolve();
              }
            });
          });
    const rtcConfiguration = {
      iceServers: [{ urls: [iceUrl], username, credential }],
      iceTransportPolicy: "relay",
      bundlePolicy: "max-bundle",
    };
    const sender = new RTCPeerConnection(rtcConfiguration);
    const receiver = new RTCPeerConnection(rtcConfiguration);
    let receivedBytes = 0;
    let echoedBytes = 0;
    let receiverChannel;
    receiver.addEventListener("datachannel", (event) => {
      receiverChannel = event.channel;
      receiverChannel.addEventListener("message", (message) => {
        receivedBytes += String(message.data).length;
        receiverChannel.send(message.data);
      });
    });
    const channel = sender.createDataChannel("public-turn-evidence", {
      ordered: true,
    });
    channel.addEventListener("message", (message) => {
      echoedBytes += String(message.data).length;
    });
    try {
      await sender.setLocalDescription(await sender.createOffer());
      await waitForGathering(sender);
      await receiver.setRemoteDescription(sender.localDescription);
      await receiver.setLocalDescription(await receiver.createAnswer());
      await waitForGathering(receiver);
      await sender.setRemoteDescription(receiver.localDescription);
      await waitFor(
        () =>
          channel.readyState === "open" &&
          receiverChannel?.readyState === "open",
        "data_channel_open_timeout",
      );
      const payload = "ananta-public-turn-evidence".repeat(2048);
      channel.send(payload);
      await waitFor(
        () => receivedBytes >= payload.length && echoedBytes >= payload.length,
        "data_channel_echo_timeout",
      );
      const stats = await sender.getStats();
      let selectedPair;
      for (const row of stats.values()) {
        if (row.type === "transport" && row.selectedCandidatePairId) {
          selectedPair = stats.get(row.selectedCandidatePairId);
        }
      }
      if (!selectedPair) {
        selectedPair = [...stats.values()].find(
          (row) =>
            row.type === "candidate-pair" &&
            row.state === "succeeded" &&
            (row.nominated === true || row.selected === true),
        );
      }
      const local = selectedPair
        ? stats.get(selectedPair.localCandidateId)
        : undefined;
      const remote = selectedPair
        ? stats.get(selectedPair.remoteCandidateId)
        : undefined;
      return {
        connected: sender.connectionState === "connected",
        senderIceState: sender.iceConnectionState,
        receiverIceState: receiver.iceConnectionState,
        localCandidateType: local?.candidateType ?? null,
        remoteCandidateType: remote?.candidateType ?? null,
        protocol: local?.protocol ?? null,
        relayProtocol: local?.relayProtocol ?? null,
        pairState: selectedPair?.state ?? null,
        bytesSent: Number(selectedPair?.bytesSent ?? 0),
        bytesReceived: Number(selectedPair?.bytesReceived ?? 0),
        applicationBytesSent: payload.length,
        applicationBytesReceived: receivedBytes,
        applicationBytesEchoed: echoedBytes,
      };
    } finally {
      channel.close();
      receiverChannel?.close();
      sender.close();
      receiver.close();
    }
  }, configuration);
}

function resultPassed(result) {
  return Boolean(
    result.connected &&
    result.senderIceState === "connected" &&
    result.receiverIceState === "connected" &&
    result.localCandidateType === "relay" &&
    result.pairState === "succeeded" &&
    result.bytesSent > 0 &&
    result.bytesReceived > 0 &&
    result.applicationBytesSent > 0 &&
    result.applicationBytesReceived >= result.applicationBytesSent &&
    result.applicationBytesEchoed >= result.applicationBytesSent,
  );
}

async function execute({ secret, host, timeoutMs }) {
  const { username, credential } = temporaryCredential(secret);
  const results = [];
  for (const [engineName, engine] of Object.entries(ENGINES)) {
    const browser = await engine.launch({ headless: true });
    try {
      const page = await browser.newPage();
      for (const [transport, urlFactory] of Object.entries(VARIANTS)) {
        let result;
        try {
          result = await probeVariant(page, {
            iceUrl: urlFactory(host),
            username,
            credential,
            timeoutMs,
          });
        } catch (error) {
          const diagnostic = String(error?.message ?? "").match(
            /(ice_gathering_timeout|data_channel_open_timeout|data_channel_echo_timeout)/u,
          )?.[1];
          throw new PublicTurnProbeError(
            `public_turn_probe_${engineName}_${transport}_${diagnostic ?? "failed"}`,
          );
        }
        results.push({ engine: engineName, transport, ...result });
      }
    } finally {
      await browser.close();
    }
  }
  const passed =
    results.length ===
      Object.keys(ENGINES).length * Object.keys(VARIANTS).length &&
    results.every(resultPassed);
  return {
    schema: "ananta.public-turn-relay-probe.v1",
    status: passed ? "passed" : "failed",
    reason_code: passed
      ? "public_turn_relay_probe_passed"
      : "public_turn_relay_probe_failed",
    public_host: host,
    credential_ttl_seconds: 600,
    engines: Object.keys(ENGINES),
    transports: Object.keys(VARIANTS),
    results,
    human_intervention_required: false,
    production_capacity: false,
  };
}

async function atomicWrite(path, payload) {
  const temporary = `${path}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await fs.rename(temporary, path);
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const secret = await readSecret();
  const report = await execute({
    secret,
    host: args.host,
    timeoutMs: args.timeoutMs,
  });
  const encoded = JSON.stringify(report);
  if (encoded.includes(secret)) {
    throw new PublicTurnProbeError("public_turn_probe_secret_exposed");
  }
  await atomicWrite(args.output, report);
  process.stdout.write(
    `${JSON.stringify({ status: report.status, reason_code: report.reason_code })}\n`,
  );
  process.exitCode = report.status === "passed" ? 0 : 1;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    const reasonCode =
      error instanceof PublicTurnProbeError
        ? error.message
        : "public_turn_probe_execution_failed";
    process.stderr.write(
      `${JSON.stringify({ status: "failed", reason_code: reasonCode })}\n`,
    );
    process.exitCode = 2;
  });
}

export {
  PublicTurnProbeError,
  execute,
  parseArguments,
  resultPassed,
  temporaryCredential,
};
