/**
 * Ananta WebRTC App — main entry point.
 *
 * Reads configuration from window.ANANTA_CONFIG (injected by Python side).
 * Expected shape:
 * {
 *   signaling_url: "wss://...",
 *   session_nonce: "...",
 *   oidc_subject_hash: "...",
 *   stun_servers: ["stun:..."],
 *   turn_servers: [{ urls: "turn:...", username: "...", credential: "..." }],
 *   datachannel_enabled: true,
 *   max_message_bytes: 65536,
 * }
 *
 * No camera/microphone is requested at any point by this file.
 * media_probe.js must be imported and called explicitly by external code.
 */

import { setupDataChannel, makePing, makePong, makeHello, makeHelloAck } from "./datachannel.js";

// ---- DOM helpers -----------------------------------------------------------

/** Map a state string to a CSS class and dot color. */
function stateClass(state) {
  const s = (state || "").toLowerCase();
  if (["connected", "open", "success", "complete"].includes(s)) return ["state-connected", "dot-green"];
  if (["connecting", "checking", "pending", "new"].includes(s)) return ["state-connecting", "dot-yellow"];
  if (["failed", "closed", "error", "disconnected"].includes(s)) return ["state-failed", "dot-red"];
  return ["state-unknown", "dot-grey"];
}

function setStatus(elementId, state, label) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const [cls, dotCls] = stateClass(state);
  el.innerHTML = `<span class="dot ${dotCls}"></span><span class="${cls}">${label || state}</span>`;
}

function setText(elementId, text, cls) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = text || "—";
  if (cls) el.className = cls;
}

let _logEntries = 0;
function log(msg, type) {
  const el = document.getElementById("log");
  if (!el) return;
  const div = document.createElement("div");
  div.className = `log-entry${type === "error" ? " log-error" : type === "ok" ? " log-ok" : ""}`;
  const ts = new Date().toISOString().substring(11, 23);
  div.textContent = `[${ts}] ${msg}`;
  el.appendChild(div);
  _logEntries++;
  // Keep at most 200 entries
  while (el.children.length > 200) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

// ---- Session state ---------------------------------------------------------

const state = {
  oidc: "unknown",        // unknown | authenticated | unauthenticated
  signaling: "disconnected", // disconnected | connecting | connected | failed
  ice: "new",             // new | checking | connected | completed | failed | disconnected | closed
  datachannel: "closed",  // closed | connecting | open
  peerId: null,
  ws: null,
  pc: null,
  dc: null,
};

function renderAll() {
  setStatus("oidc-state", state.oidc);
  setStatus("signaling-state", state.signaling);
  setStatus("ice-state", state.ice);
  setStatus("datachannel-state", state.datachannel);
  setText(
    "subject-hash",
    (window.ANANTA_CONFIG && window.ANANTA_CONFIG.oidc_subject_hash) || "—",
    state.oidc === "authenticated" ? "state-connected" : "state-unknown"
  );
  setText("peer-id", state.peerId || "—");
}

// ---- Config ----------------------------------------------------------------

function getConfig() {
  return window.ANANTA_CONFIG || {};
}

function signalingMessage(type, cfg, payload = {}, recipientId = "") {
  return {
    type,
    session_id: cfg.session_id || "default",
    sender_id: cfg.browser_peer_id || "browser",
    recipient_id: recipientId,
    payload,
    session_nonce: cfg.session_nonce || "",
    message_id: crypto.randomUUID(),
    timestamp: Date.now() / 1000,
  };
}

// ---- Signaling over WebSocket ----------------------------------------------

function connectSignaling(cfg) {
  if (!cfg.signaling_url) {
    log("No signaling_url in ANANTA_CONFIG — staying disconnected", "error");
    return;
  }
  state.signaling = "connecting";
  renderAll();
  log(`Connecting signaling: ${cfg.signaling_url}`);

  let ws;
  try {
    ws = new WebSocket(cfg.signaling_url);
  } catch (err) {
    state.signaling = "failed";
    renderAll();
    log(`WebSocket creation failed: ${err.message}`, "error");
    return;
  }

  state.ws = ws;

  ws.onopen = () => {
    state.signaling = "connected";
    renderAll();
    log("Signaling connected", "ok");
    ws.send(JSON.stringify(signalingMessage("join", cfg, {
      oidc_subject_hash: cfg.oidc_subject_hash || "",
    })));
  };

  ws.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      log("Signaling: unparseable message", "error");
      return;
    }
    handleSignalingMessage(msg, cfg);
  };

  ws.onerror = () => {
    state.signaling = "failed";
    renderAll();
    log("Signaling error", "error");
  };

  ws.onclose = () => {
    state.signaling = "disconnected";
    state.ws = null;
    renderAll();
    log("Signaling disconnected");
  };
}

// ---- WebRTC PeerConnection --------------------------------------------------

function buildIceServers(cfg) {
  const servers = [];
  for (const url of (cfg.stun_servers || [])) {
    servers.push({ urls: url });
  }
  for (const t of (cfg.turn_servers || [])) {
    servers.push(t);
  }
  return servers;
}

function createPeerConnection(cfg) {
  const iceServers = buildIceServers(cfg);
  const pc = new RTCPeerConnection({ iceServers });

  pc.oniceconnectionstatechange = () => {
    state.ice = pc.iceConnectionState;
    renderAll();
    log(`ICE: ${pc.iceConnectionState}`);
  };

  pc.onicecandidate = (ev) => {
    if (ev.candidate && state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({
        ...signalingMessage("ice_candidate", cfg, { candidate: ev.candidate }, state.peerId || ""),
      }));
    }
  };

  pc.ondatachannel = (ev) => {
    log("Incoming DataChannel from peer", "ok");
    attachDataChannel(ev.channel, cfg);
  };

  return pc;
}

function attachDataChannel(dc, cfg) {
  state.dc = dc;
  state.datachannel = "connecting";
  renderAll();

  setupDataChannel(dc, {
    onOpen: () => {
      state.datachannel = "open";
      renderAll();
      log("DataChannel open", "ok");
      // Send hello
      const hello = makeHello(cfg.session_nonce || "", state.peerId || "browser");
      dc.send(JSON.stringify(hello));
    },
    onClose: () => {
      state.datachannel = "closed";
      state.dc = null;
      renderAll();
      log("DataChannel closed");
    },
    onError: (err) => {
      state.datachannel = "error";
      renderAll();
      log(`DataChannel error: ${err.message || err}`, "error");
    },
    onMessage: (msg) => handleDataChannelMessage(msg, cfg),
  });
}

// ---- Message handlers ------------------------------------------------------

async function handleSignalingMessage(msg, cfg) {
  const payload = msg.payload || {};
  switch (msg.type) {
    case "joined":
      state.oidc = "authenticated";
      state.peerId = msg.peer_id || msg.sender_id || payload.peer_id || null;
      renderAll();
      log(`Joined session. Peer ID: ${state.peerId}`, "ok");
      break;

    case "offer": {
      log("Received SDP offer");
      if (!state.pc) state.pc = createPeerConnection(cfg);
      await state.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp || msg.sdp));
      const answer = await state.pc.createAnswer();
      await state.pc.setLocalDescription(answer);
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          ...signalingMessage("answer", cfg, { sdp: answer }, state.peerId || ""),
        }));
      }
      break;
    }

    case "answer": {
      log("Received SDP answer");
      if (state.pc) {
        await state.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp || msg.sdp));
      }
      break;
    }

    case "ice_candidate": {
      const candidate = payload.candidate || msg.candidate;
      if (state.pc && candidate) {
        try {
          await state.pc.addIceCandidate(new RTCIceCandidate(candidate));
        } catch (err) {
          log(`ICE candidate error: ${err.message}`, "error");
        }
      }
      break;
    }

    case "error":
      log(`Signaling error: ${msg.message || JSON.stringify(msg)}`, "error");
      break;

    default:
      log(`Signaling unknown type: ${msg.type}`);
  }
}

function handleDataChannelMessage(msg, cfg) {
  switch (msg.type) {
    case "ping": {
      const pong = makePong(msg);
      if (state.dc && state.dc.readyState === "open") {
        state.dc.send(JSON.stringify(pong));
      }
      break;
    }
    case "pong":
      log(`Pong received. latency=${msg.payload.latency_ms}ms`);
      break;

    case "hello":
      log(`Hello from peer: ${msg.payload.peer_id}`);
      if (state.dc && state.dc.readyState === "open") {
        const ack = makeHelloAck(msg, state.peerId || "browser");
        state.dc.send(JSON.stringify(ack));
      }
      break;

    case "hello_ack":
      log(`Hello ack from peer`, "ok");
      break;

    case "artifact_offer":
      log(`Artifact offer: ${msg.payload.filename} (${msg.payload.size} bytes)`);
      // Dispatch a custom DOM event so the host page can handle accept/reject
      window.dispatchEvent(new CustomEvent("ananta:artifact_offer", { detail: msg }));
      break;

    case "artifact_complete":
      log(`Artifact transfer complete: ${msg.payload.offer_id}`, "ok");
      window.dispatchEvent(new CustomEvent("ananta:artifact_complete", { detail: msg }));
      break;

    case "error":
      log(`DataChannel error msg: ${msg.payload.message || ""}`, "error");
      break;

    default:
      log(`DataChannel: unknown type ${msg.type}`);
  }
}

// ---- Init ------------------------------------------------------------------

function init() {
  const cfg = getConfig();

  // Show OIDC state from config
  if (cfg.oidc_subject_hash) {
    state.oidc = "authenticated";
  } else {
    state.oidc = "unauthenticated";
  }

  renderAll();
  log("Ananta WebRTC app initialized");

  if (!cfg.signaling_url) {
    log("No signaling_url — staying disconnected (offline mode)");
    renderAll();
    return;
  }

  connectSignaling(cfg);

  // Expose session control API on window for host page integration
  window.anantaWebRtc = {
    getState: () => ({ ...state, ws: undefined, pc: undefined, dc: undefined }),
    acceptArtifact: (offerMsg) => {
      if (state.dc && state.dc.readyState === "open") {
        const { makeArtifactAccept } = import("./datachannel.js");
        // inline to avoid async complexity
        state.dc.send(JSON.stringify({
          type: "artifact_accept",
          protocol_version: 1,
          session_nonce: cfg.session_nonce || "",
          message_id: crypto.randomUUID(),
          timestamp: Date.now() / 1000,
          payload: { offer_id: offerMsg.payload.offer_id },
        }));
      }
    },
    rejectArtifact: (offerMsg, reason) => {
      if (state.dc && state.dc.readyState === "open") {
        state.dc.send(JSON.stringify({
          type: "artifact_reject",
          protocol_version: 1,
          session_nonce: cfg.session_nonce || "",
          message_id: crypto.randomUUID(),
          timestamp: Date.now() / 1000,
          payload: { offer_id: offerMsg.payload.offer_id, reason: reason || "" },
        }));
      }
    },
    disconnect: () => {
      if (state.ws) state.ws.close();
      if (state.pc) state.pc.close();
      if (state.dc) state.dc.close();
    },
  };
}

// Run after DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
