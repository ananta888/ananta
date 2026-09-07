"""Passive fixed-code diagnostics; never retain PCM/progress messages or change a node."""

INSTALL = """(() => {
  window.__testSpeechErrors = [];
  const Native = window.AudioWorkletNode;
  if (!Native) return;
  window.AudioWorkletNode = class extends Native {
    constructor(...args) {
      super(...args);
      if (args[1] !== 'ananta-machine-speech-v1') return;
      this.port.addEventListener('message', ({data}) => {
        const allowed = ['meet_speech_worklet_profile_invalid', 'meet_speech_worklet_input_invalid',
          'meet_speech_worklet_expired_or_invalid', 'meet_speech_worklet_underrun'];
        if (data?.type === 'error' && allowed.includes(data.code)) {
          window.__testSpeechErrors.push(data.code);
          if (window.__testSpeechErrors.length > 8) window.__testSpeechErrors.shift();
        }
      });
    }
  };
})()"""
