import assert from "node:assert/strict";
import test from "node:test";

import {
  parseArguments,
  resultPassed,
  temporaryCredential,
} from "./public_turn_relay_probe.mjs";

test("TURN REST credentials are short-lived and do not contain the secret", () => {
  const credential = temporaryCredential("test-secret-with-sufficient-entropy");

  assert.match(credential.username, /:ananta-public-evidence$/u);
  assert.doesNotMatch(credential.credential, /test-secret/u);
  assert.ok(credential.credential.length > 20);
});

test("relay result requires selected relay and bidirectional traffic", () => {
  const result = {
    connected: true,
    senderIceState: "connected",
    receiverIceState: "connected",
    localCandidateType: "relay",
    pairState: "succeeded",
    bytesSent: 200,
    bytesReceived: 100,
    applicationBytesSent: 50,
    applicationBytesReceived: 50,
    applicationBytesEchoed: 50,
  };

  assert.equal(resultPassed(result), true);
  assert.equal(resultPassed({ ...result, localCandidateType: "host" }), false);
  assert.equal(resultPassed({ ...result, applicationBytesEchoed: 0 }), false);
});

test("arguments remain bounded to a public host and timeout", () => {
  assert.deepEqual(
    parseArguments([
      "--host",
      "webrtc.ananta.de",
      "--output",
      "/tmp/result.json",
      "--timeout-ms",
      "20000",
    ]),
    {
      host: "webrtc.ananta.de",
      output: "/tmp/result.json",
      timeoutMs: 20000,
    },
  );
  assert.throws(
    () => parseArguments(["--host", "https://invalid.test"]),
    /public_turn_probe_arguments_invalid/u,
  );
});
