"""One delegated PCM asset; the timer executes frames, never tasks or Hub policy."""

START = """([token, receipt, authority, encoded]) => {
  const prior = window.__anantaPcmFeeder;
  if (prior && prior.state === 'open') throw new Error('meet_speech_feeder_busy');
  if (typeof encoded !== 'string' || !encoded.length || encoded.length > 2352000
    || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error('meet_speech_feeder_pcm_invalid');
  const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
  encoded = null;
  if (!bytes.length || bytes.length !== receipt.totalSamples * 2 || bytes.length > 1764000) {
    bytes.fill(0); throw new Error('meet_speech_feeder_pcm_invalid');
  }
  const source = window.anantaMachine.speech;
  const expectedLease = JSON.stringify(Object.entries(authority.lease).sort());
  const phase = {token, state: 'open', sent: 0, played: 0, source: null};
  let timer, wall = Date.now(), mono = performance.now(), controllerUntil = 0;
  const stop = state => {
    if (phase.state !== 'open') return;
    phase.state = state; clearInterval(timer); bytes.fill(0);
    if (state !== 'completed' && source.status().generation === receipt.generation) source.close();
  };
  phase.cancel = () => stop('closed');
  const local = () => {
    const now = Date.now(), currentMono = performance.now(), machine = window.anantaMachine;
    const current = machine.status();
    if (window.__anantaPcmFeeder !== phase || location.href !== authority.url
      || !Number.isFinite(now) || !Number.isFinite(currentMono) || now < wall || currentMono < mono
      || now >= authority.deadline || now >= receipt.expiresAt || current.joined !== true
      || machine.chat.status().open !== true
      || JSON.stringify(Object.entries(current.lease || {}).sort()) !== expectedLease) {
      throw new Error('meet_speech_feeder_authority_changed');
    }
    wall = now; mono = currentMono;
    return {now, currentMono};
  };
  const check = () => {
    const {now, currentMono} = local();
    if (now >= authority.hubUntil || currentMono >= controllerUntil) {
      throw new Error('meet_speech_feeder_hub_stale');
    }
  };
  phase.pulse = next => {
    if (phase.state === 'completed') return true; // Observation only; never restart or renew completed playback.
    if (phase.state !== 'open') return false;
    try {
      check();
      if (next.url !== authority.url || next.deadline !== authority.deadline
        || JSON.stringify(Object.entries(next.lease || {}).sort()) !== expectedLease
        || !Number.isSafeInteger(next.hubUntil) || next.hubUntil <= wall || next.hubUntil > wall + 2500) {
        throw new Error('meet_speech_feeder_pulse_invalid');
      }
      authority.hubUntil = next.hubUntil;
      controllerUntil = mono + Math.min(2500, next.hubUntil - wall);
      return true;
    } catch { stop('failed'); return false; }
  };
  const progress = value => {
    const done = value?.state === 'completed';
    if (!value || Object.keys(value).sort().join() !== 'bufferedSamples,generation,playedSamples,receivedSamples,state'
      || !['open', 'completed'].includes(value.state)
      || !['generation', 'receivedSamples', 'playedSamples', 'bufferedSamples']
        .every(k => Number.isSafeInteger(value[k]))
      || value.generation !== receipt.generation + Number(done) || value.receivedSamples !== phase.sent
      || value.playedSamples < phase.played || value.playedSamples > phase.sent || phase.sent > receipt.totalSamples
      || value.bufferedSamples !== (done ? 0 : phase.sent - value.playedSamples)
      || value.bufferedSamples < 0 || value.bufferedSamples > 4410
      || done && value.playedSamples !== receipt.totalSamples) throw new Error('meet_speech_feeder_progress_invalid');
    phase.played = value.playedSamples; phase.source = {...value};
    if (done) stop('completed');
    return value;
  };
  const tick = () => {
    if (phase.state !== 'open') return;
    try {
      check();
      const value = progress(source.status());
      if (phase.state !== 'open') return;
      let available = 4410 - value.bufferedSamples;
      for (let count = 0; count < 10 && phase.sent < receipt.totalSamples; count++) {
        const samples = Math.min(441, receipt.totalSamples - phase.sent);
        if (available < samples) break;
        check();
        const start = phase.sent * 2, end = start + samples * 2;
        const pcm = btoa(String.fromCharCode(...bytes.subarray(start, end)));
        source.push(receipt.generation, phase.sent, pcm);
        bytes.fill(0, start, end); phase.sent += samples; available -= samples;
      }
      progress(source.status());
    } catch { stop('failed'); }
  };
  phase.status = () => {
    if (phase.state === 'open') { try { check(); progress(source.status()); } catch { stop('failed'); } }
    return {state: phase.state, sent: phase.sent, source: phase.source};
  };
  window.__anantaPcmFeeder = phase;
  try {
    local();
    if (!Number.isSafeInteger(authority.hubUntil) || authority.hubUntil <= wall || authority.hubUntil > wall + 2500) {
      throw new Error('meet_speech_feeder_pulse_invalid');
    }
    controllerUntil = mono + Math.min(2500, authority.hubUntil - wall);
    check();
    timer = setInterval(tick, 20);
    tick();
  } catch { stop('failed'); }
}"""

STATUS = """token => {
  const phase = window.__anantaPcmFeeder;
  return phase?.token === token ? phase.status() : null;
}"""

PULSE = """([token, authority]) => {
  const phase = window.__anantaPcmFeeder;
  return phase?.token === token && phase.pulse(authority);
}"""

CLOSE = """token => {
  const phase = window.__anantaPcmFeeder;
  if (phase?.token === token) { phase.cancel(); delete window.__anantaPcmFeeder; }
}"""
