# Fairphone Voxtral Offline Prep

Small standalone preparation tool for testing Voxtral-style offline speech transcription on a Fairphone 6 with Termux.

This is intentionally not wired into Ananta yet. It is a safe preparation layer:

1. install Termux packages
2. build `llama.cpp`
3. prepare a local model directory
4. record or copy a small WAV file
5. run a placeholder transcription command once a compatible Voxtral runner/model is available

## Target

First success criterion:

```text
Fairphone 6 offline
→ 5 second audio sample
→ terminal text output
```

## Files

```text
install-termux.sh      Install required Termux packages
build-llama-cpp.sh     Clone/build llama.cpp in Termux
record-test-audio.sh   Record a short test WAV using termux-api
transcribe-test.sh     Safe runner wrapper for a Voxtral-compatible CLI
check-env.sh           Show environment and expected paths
```

## Quick start on Fairphone 6

Install Termux from F-Droid, then inside Termux:

```bash
pkg update
pkg install git

git clone https://github.com/ananta888/ananta.git
cd ananta/tools/fairphone_voxtral_offline

bash install-termux.sh
bash build-llama-cpp.sh
bash check-env.sh
```

Record a short test file:

```bash
bash record-test-audio.sh
```

Then place a compatible Voxtral GGUF/model bundle in:

```text
~/models/voxtral/
```

and run:

```bash
bash transcribe-test.sh ~/models/voxtral/YOUR_MODEL.gguf ./samples/test.wav
```

## Important limitation

Normal `llama-cli` text inference is not enough for Voxtral audio transcription. A compatible Voxtral audio runner is required. This tool prepares the Fairphone environment and makes that limitation explicit instead of hiding it.

If no Voxtral runner is found, `transcribe-test.sh` exits with a clear message and does not fake transcription.

## Safety

This tool:

- does not require root
- does not modify Android system files
- does not install Ananta services
- does not connect to Ananta Hub
- does not upload audio anywhere
- only works on local files

