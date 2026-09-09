import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { once } from "node:events";
import { createPiFetch } from "../agent/cli_backends/pi_http_transport.mjs";

const endpoint = "https://model.invalid/v1/chat/completions";
const payload = { model: "selected", stream: true, max_tokens: 1024, messages: [] };
const request = (body = payload) => ({ method: "POST", body: JSON.stringify(body) });
function fixture(response = new Response("data: [DONE]\n\n")) {
  const calls = [];
  const fetch = createPiFetch({ baseUrl: "https://model.invalid/v1/", modelId: "selected", maxTokens: 1024,
    fetchImplementation: async (...args) => { calls.push(args); return response; },
  });
  return { calls, fetch, response };
}

test("one exact POST retains the abort signal and refuses redirects", async () => {
  const { fetch, calls, response } = fixture();
  const signal = new AbortController().signal;
  assert.equal(await fetch(new URL(endpoint), { ...request(), signal, redirect: "follow" }), response);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].redirect, "error");
  assert.equal(calls[0][1].signal, signal);
  await assert.rejects(fetch(endpoint, request()), /pi_http_request_not_authorized/);
  assert.equal(calls.length, 1);
});

for (const destination of ["https://other.invalid/v1/chat/completions", endpoint + "?key=private",
  endpoint + "#fragment", endpoint.replace("https:", "http:"), "https://private@model.invalid/v1/chat/completions"]) {
  test(`different destination rejected: ${destination}`, async () => {
    const { fetch, calls } = fixture();
    await assert.rejects(fetch(destination, request()), /pi_http_request_not_authorized/);
    assert.equal(calls.length, 0);
  });
}
for (const init of [{ ...request(), method: "GET" }, { ...request(), body: "not-json" },
  { ...request(), body: "x".repeat(2_000_001) }, request({ ...payload, model: "other" }),
  request({ ...payload, stream: false }), request({ ...payload, max_tokens: 1025 }),
  request({ ...payload, max_completion_tokens: 1025 }), request({ ...payload, max_tokens: undefined }),
  request({ ...payload, tools: [{}] }), request({ ...payload, tools: {} }),
  request({ ...payload, functions: [] }), request({ ...payload, tool_choice: "auto" })]) {
  test("invalid HTTP method/body denied before network", async () => {
    const { fetch, calls } = fixture();
    await assert.rejects(fetch(endpoint, init), /pi_http_request_not_authorized/);
    assert.equal(calls.length, 0);
  });
}
test("opaque Request bodies are not read or forwarded", async () => {
  const { fetch, calls } = fixture();
  await assert.rejects(fetch(new Request(endpoint, request())), /pi_http_request_not_authorized/);
  assert.equal(calls.length, 0);
});
test("completion-token field accepted at the same fixed ceiling", async () => {
  const { fetch, calls } = fixture();
  await fetch(endpoint, request({ ...payload, max_tokens: undefined, max_completion_tokens: 1024 }));
  assert.equal(calls.length, 1);
});
for (const status of [301, 302, 303, 307, 308]) {
  test(`redirect ${status} returned by a custom transport is denied`, async () => {
    const { fetch, response } = fixture(new Response("", { status, headers: { Location: "/other" } }));
    await assert.rejects(fetch(endpoint, request()), /pi_http_request_not_authorized/);
    assert.equal(response.body.locked, false);
  });
}
for (const response of [{ redirected: true, status: 200 }, { url: "https://other.invalid", status: 200 }]) {
  test("custom transport cannot hide a changed destination", async () => {
    const { fetch } = fixture(response);
    await assert.rejects(fetch(endpoint, request()), /pi_http_request_not_authorized/);
  });
}
test("a failed network request still consumes the single attempt", async () => {
  let calls = 0;
  const fetch = createPiFetch({ baseUrl: "https://model.invalid/v1", modelId: "selected", maxTokens: 1024,
    fetchImplementation: async () => { calls++; throw new Error("network failure"); },
  });
  await assert.rejects(fetch(endpoint, request()), /network failure/);
  await assert.rejects(fetch(endpoint, request()), /pi_http_request_not_authorized/);
  assert.equal(calls, 1);
});
for (const baseUrl of ["invalid", "ftp://model.invalid", "https://private@model.invalid", "https://model.invalid?q=1"])
  test("invalid endpoint configuration rejected", () => {
    assert.throws(() => createPiFetch({ baseUrl, modelId: "selected", maxTokens: 1024 }), /pi_http_request_not_authorized/);
  });

test("real loopback 307 never contacts its target", { timeout: 5000 }, async () => {
  let initial = 0, redirected = 0;
  const server = createServer((request, response) => {
    request.resume();
    if (request.url === "/redirected") { redirected++; response.end("unexpected"); return; }
    initial++;
    response.writeHead(307, { Location: "/redirected" });
    response.end();
  });
  server.listen(0, "127.0.0.1");
  try {
    await once(server, "listening");
    const baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
    const fetch = createPiFetch({ baseUrl, modelId: "selected", maxTokens: 1024 });
    await assert.rejects(fetch(baseUrl + "/chat/completions", { ...request(), signal: AbortSignal.timeout(2000) }));
    assert.equal(initial, 1);
    assert.equal(redirected, 0);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});
