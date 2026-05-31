/**
 * Ananta media capability probe — disabled by default.
 *
 * This module is NOT auto-imported by app.js.
 * It must be explicitly imported and `probeMediaCapabilities()` explicitly called
 * by host code when the user has requested it.
 *
 * No camera or microphone access is requested without calling probeMediaCapabilities().
 */

/**
 * @typedef {"unsupported"|"permission_denied"|"device_missing"|"success"} ProbeResult
 */

/**
 * Probe a single media constraint (e.g. {video: true} or {audio: true}).
 * @param {MediaStreamConstraints} constraints
 * @returns {Promise<ProbeResult>}
 */
async function probeConstraint(constraints) {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getUserMedia !== "function"
  ) {
    return "unsupported";
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    // Stop all tracks immediately — we only needed to check permissions/availability
    for (const track of stream.getTracks()) {
      track.stop();
    }
    return "success";
  } catch (err) {
    if (!err || !err.name) return "unsupported";
    if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
      return "permission_denied";
    }
    if (
      err.name === "NotFoundError" ||
      err.name === "DevicesNotFoundError" ||
      err.name === "NotReadableError"
    ) {
      return "device_missing";
    }
    return "unsupported";
  }
}

/**
 * Probe screen share availability (display media).
 * @returns {Promise<ProbeResult>}
 */
async function probeScreenShare() {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices ||
    typeof navigator.mediaDevices.getDisplayMedia !== "function"
  ) {
    return "unsupported";
  }
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    for (const track of stream.getTracks()) {
      track.stop();
    }
    return "success";
  } catch (err) {
    if (!err || !err.name) return "unsupported";
    if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
      return "permission_denied";
    }
    return "unsupported";
  }
}

/**
 * Probe whether the WebRTC API itself is available.
 * @returns {"supported"|"unsupported"}
 */
function probeWebRtcApi() {
  if (
    typeof RTCPeerConnection !== "undefined" &&
    typeof RTCSessionDescription !== "undefined"
  ) {
    return "supported";
  }
  return "unsupported";
}

/**
 * Run all media capability probes.
 *
 * IMPORTANT: This function requests camera/microphone/screen permissions.
 * Call ONLY when the user has explicitly triggered this action.
 *
 * @returns {Promise<{
 *   camera: ProbeResult,
 *   microphone: ProbeResult,
 *   audio: ProbeResult,
 *   video: ProbeResult,
 *   screen_share: ProbeResult,
 *   webrtc_api: "supported"|"unsupported",
 *   error: string|null,
 * }>}
 */
export async function probeMediaCapabilities() {
  const result = {
    camera: "unsupported",
    microphone: "unsupported",
    audio: "unsupported",
    video: "unsupported",
    screen_share: "unsupported",
    webrtc_api: probeWebRtcApi(),
    error: null,
  };

  try {
    result.camera = await probeConstraint({ video: true });
    result.video = result.camera; // alias

    result.microphone = await probeConstraint({ audio: true });
    result.audio = result.microphone; // alias

    result.screen_share = await probeScreenShare();
  } catch (err) {
    result.error = err && err.message ? err.message : String(err);
  }

  return result;
}
