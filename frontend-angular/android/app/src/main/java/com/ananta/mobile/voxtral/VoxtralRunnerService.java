package com.ananta.mobile.voxtral;

import android.app.Service;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.os.IBinder;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

public class VoxtralRunnerService extends Service {
    public static final String ACTION_RUN = "com.ananta.mobile.voxtral.ACTION_RUN";

    public static final String EXTRA_MODE = "mode";
    public static final String EXTRA_RUNNER_PATH = "runnerPath";
    public static final String EXTRA_MODEL_PATH = "modelPath";
    public static final String EXTRA_AUDIO_PATH = "audioPath";
    public static final String EXTRA_LOW_MEMORY_MODE = "lowMemoryMode";
    public static final String EXTRA_RESULT_PATH = "resultPath";
    public static final String EXTRA_HEARTBEAT_PATH = "heartbeatPath";
    public static final String EXTRA_TIMEOUT_MS = "timeoutMs";

    private static final int MAX_PROCESS_OUTPUT_CHARS = 64 * 1024;
    private static final long DEFAULT_TIMEOUT_MS = 120_000L;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || !ACTION_RUN.equals(intent.getAction())) {
            stopSelfResult(startId);
            return START_NOT_STICKY;
        }
        new Thread(() -> handleRun(startId, intent), "voxtral-runner-service").start();
        return START_NOT_STICKY;
    }

    private void handleRun(int startId, Intent intent) {
        String resultPath = String.valueOf(intent.getStringExtra(EXTRA_RESULT_PATH) == null ? "" : intent.getStringExtra(EXTRA_RESULT_PATH)).trim();
        String heartbeatPath = String.valueOf(intent.getStringExtra(EXTRA_HEARTBEAT_PATH) == null ? "" : intent.getStringExtra(EXTRA_HEARTBEAT_PATH)).trim();
        JSONObject result = new JSONObject();
        result.put("status", "error");
        result.put("error", "runner_service_unknown_error");
        result.put("rawOutput", "");
        try {
            String mode = String.valueOf(intent.getStringExtra(EXTRA_MODE) == null ? "transcribe" : intent.getStringExtra(EXTRA_MODE)).trim().toLowerCase(Locale.US);
            File runnerFile = new File(String.valueOf(intent.getStringExtra(EXTRA_RUNNER_PATH) == null ? "" : intent.getStringExtra(EXTRA_RUNNER_PATH)).trim());
            File modelFile = new File(String.valueOf(intent.getStringExtra(EXTRA_MODEL_PATH) == null ? "" : intent.getStringExtra(EXTRA_MODEL_PATH)).trim());
            String audioRaw = String.valueOf(intent.getStringExtra(EXTRA_AUDIO_PATH) == null ? "" : intent.getStringExtra(EXTRA_AUDIO_PATH)).trim();
            File audioFile = audioRaw.isBlank() ? null : new File(audioRaw);
            boolean lowMemoryMode = intent.getBooleanExtra(EXTRA_LOW_MEMORY_MODE, false);
            long timeoutMs = Math.max(5_000L, intent.getLongExtra(EXTRA_TIMEOUT_MS, DEFAULT_TIMEOUT_MS));

            if (!runnerFile.isFile()) {
                throw new IOException("Runner file not found.");
            }
            if (!modelFile.isFile()) {
                throw new IOException("Model file not found.");
            }
            if ("transcribe".equals(mode) && (audioFile == null || !audioFile.isFile())) {
                throw new IOException("Audio file not found.");
            }

            AtomicBoolean alive = new AtomicBoolean(true);
            Thread heartbeatThread = startHeartbeatThread(heartbeatPath, alive);
            try {
                String output;
                if ("probe".equals(mode)) {
                    output = runProbeSync(runnerFile, modelFile, timeoutMs);
                } else {
                    output = runTranscriptionSync(runnerFile, modelFile, audioFile, lowMemoryMode, timeoutMs);
                }
                result.put("status", "ok");
                result.put("rawOutput", output);
                result.put("transcript", extractTranscript(output));
            } finally {
                alive.set(false);
                if (heartbeatThread != null) {
                    try {
                        heartbeatThread.join(1_500L);
                    } catch (InterruptedException interruptedException) {
                        Thread.currentThread().interrupt();
                    }
                }
            }
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage() == null ? error.toString() : error.getMessage()).trim();
            result.put("status", "error");
            result.put("error", message.isBlank() ? "runner_service_failure" : message);
        }

        writeJsonResult(resultPath, result);
        stopSelfResult(startId);
    }

    private Thread startHeartbeatThread(String heartbeatPath, AtomicBoolean alive) {
        if (heartbeatPath == null || heartbeatPath.isBlank()) return null;
        Thread thread = new Thread(() -> {
            File heartbeatFile = new File(heartbeatPath);
            while (alive.get()) {
                try (FileOutputStream output = new FileOutputStream(heartbeatFile, false)) {
                    output.write(String.valueOf(System.currentTimeMillis()).getBytes(StandardCharsets.UTF_8));
                    output.flush();
                } catch (Exception ignored) {
                }
                try {
                    Thread.sleep(1000L);
                } catch (InterruptedException interruptedException) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }, "voxtral-runner-heartbeat");
        thread.start();
        return thread;
    }

    private void writeJsonResult(String path, JSONObject payload) {
        if (path == null || path.isBlank()) return;
        File out = new File(path);
        try (FileOutputStream output = new FileOutputStream(out, false)) {
            output.write(payload.toString().getBytes(StandardCharsets.UTF_8));
            output.flush();
        } catch (Exception ignored) {
        }
    }

    private String runTranscriptionSync(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode, long timeoutMs) throws IOException, InterruptedException {
        List<String> command = buildRunnerCommand(runnerFile, modelFile, audioFile, lowMemoryMode);
        return executeWithLinkerFallback(command, timeoutMs);
    }

    private String runProbeSync(File runnerFile, File modelFile, long timeoutMs) throws IOException, InterruptedException {
        List<String> command = buildRunnerProbeCommand(runnerFile, modelFile);
        return executeWithLinkerFallback(command, timeoutMs);
    }

    private String executeWithLinkerFallback(List<String> command, long timeoutMs) throws IOException, InterruptedException {
        String linkerPath = resolveSystemLinkerPath();
        String linkerError = null;
        if (linkerPath != null && !linkerPath.isBlank()) {
            try {
                return executeRunnerViaShellLinker(linkerPath, command, timeoutMs);
            } catch (IOException error) {
                linkerError = String.valueOf(error.getMessage() == null ? "" : error.getMessage());
                if (containsMissingAudioTensorError(linkerError) || containsUnsupportedModelArchitecture(linkerError)) {
                    throw error;
                }
            }
        }
        try {
            return executeRunnerCommand(command, timeoutMs);
        } catch (IOException directError) {
            String message = String.valueOf(directError.getMessage() == null ? "" : directError.getMessage());
            if (linkerError != null && !linkerError.isBlank()) {
                throw new IOException("Runner linker path failed: " + linkerError + "\nRunner direct failed: " + message);
            }
            throw directError;
        }
    }

    private String executeRunnerViaShellLinker(String linkerPath, List<String> runnerCommand, long timeoutMs) throws IOException, InterruptedException {
        String runnerBinaryPath = resolveRunnerBinaryPath(runnerCommand);
        if (runnerBinaryPath == null || runnerBinaryPath.isBlank()) {
            throw new IOException("Runner command is empty.");
        }
        File runnerBinary = new File(runnerBinaryPath);
        File runnerDir = runnerBinary.getParentFile();
        String nativeLibDir = resolveNativeLibDir();
        String ldLibraryPath = buildRunnerLibraryPath(runnerDir, nativeLibDir);
        StringBuilder shellCommand = new StringBuilder();
        shellCommand
                .append("LD_LIBRARY_PATH=")
                .append(shQuote(ldLibraryPath))
                .append(" ")
                .append("exec ")
                .append(shQuote(linkerPath))
                .append(" ")
                .append(joinShellArgs(runnerCommand));

        Process process = new ProcessBuilder("/system/bin/sh", "-lc", shellCommand.toString())
                .redirectErrorStream(true)
                .start();
        ProcessOutput processOutput = collectProcessOutput(process, timeoutMs);
        if (processOutput.exitCode != 0) {
            if (containsMissingAudioTensorError(processOutput.output)) {
                throw new IOException("Model file is not compatible with Voxtral speech backend: required audio tensors are missing. Use a realtime Voxtral GGUF.");
            }
            if (containsUnsupportedModelArchitecture(processOutput.output)) {
                throw new IOException("Runner is incompatible with model architecture 'voxtral4b'. Use a Voxtral-compatible runner (not plain llama.cpp).");
            }
            throw new IOException("Runner failed with exit code " + processOutput.exitCode + "\n" + processOutput.output);
        }
        return processOutput.output;
    }

    private String executeRunnerCommand(List<String> command, long timeoutMs) throws IOException, InterruptedException {
        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);
        String runnerBinaryPath = resolveRunnerBinaryPath(command);
        if (runnerBinaryPath != null && !runnerBinaryPath.isBlank()) {
            File runnerBinary = new File(runnerBinaryPath);
            File runnerDir = runnerBinary.getParentFile();
            String nativeLibDir = resolveNativeLibDir();
            String existing = pb.environment().get("LD_LIBRARY_PATH");
            StringBuilder ld = new StringBuilder();
            if (runnerDir != null) {
                ld.append(runnerDir.getAbsolutePath());
            }
            if (nativeLibDir != null && !nativeLibDir.isBlank()) {
                if (ld.length() > 0) ld.append(':');
                ld.append(nativeLibDir);
            }
            if (existing != null && !existing.isBlank()) {
                if (ld.length() > 0) ld.append(':');
                ld.append(existing);
            }
            if (ld.length() > 0) {
                pb.environment().put("LD_LIBRARY_PATH", ld.toString());
            }
        }
        Process process = pb.start();
        ProcessOutput processOutput = collectProcessOutput(process, timeoutMs);
        if (processOutput.exitCode != 0) {
            if (containsMissingAudioTensorError(processOutput.output)) {
                throw new IOException("Model file is not compatible with Voxtral speech backend: required audio tensors are missing. Use a realtime Voxtral GGUF.");
            }
            if (containsUnsupportedModelArchitecture(processOutput.output)) {
                throw new IOException("Runner is incompatible with model architecture 'voxtral4b'. Use a Voxtral-compatible runner (not plain llama.cpp).");
            }
            throw new IOException("Runner failed with exit code " + processOutput.exitCode + "\n" + processOutput.output);
        }
        return processOutput.output;
    }

    private ProcessOutput collectProcessOutput(Process process, long timeoutMs) throws IOException, InterruptedException {
        StringBuilder outputBuilder = new StringBuilder(Math.min(MAX_PROCESS_OUTPUT_CHARS, 8192));
        Thread readerThread = new Thread(() -> {
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                char[] buffer = new char[1024];
                int read;
                while ((read = reader.read(buffer)) != -1) {
                    synchronized (outputBuilder) {
                        int canAppend = MAX_PROCESS_OUTPUT_CHARS - outputBuilder.length();
                        if (canAppend <= 0) break;
                        outputBuilder.append(buffer, 0, Math.min(read, canAppend));
                    }
                }
            } catch (Exception ignored) {
            }
        }, "voxtral-runner-output");
        readerThread.start();

        boolean finished = process.waitFor(timeoutMs, TimeUnit.MILLISECONDS);
        if (!finished) {
            process.destroy();
            if (!process.waitFor(2, TimeUnit.SECONDS)) {
                process.destroyForcibly();
            }
            throw new IOException("Runner timeout after " + timeoutMs + "ms");
        }
        readerThread.join(1500L);
        int exitCode = process.exitValue();
        String output;
        synchronized (outputBuilder) {
            output = outputBuilder.toString().trim();
        }
        return new ProcessOutput(exitCode, output);
    }

    private List<String> buildRunnerCommand(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            command.add(audioFile.getAbsolutePath());
            return command;
        }
        if (shouldInjectVoxtralBackend(runnerFile)) {
            command.add("--backend");
            command.add("voxtral4b");
            if (lowMemoryMode) {
                command.add("-n");
                command.add("96");
                command.add("-ck");
                command.add("4");
            }
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        command.add("-f");
        command.add(audioFile.getAbsolutePath());
        return command;
    }

    private List<String> buildRunnerProbeCommand(File runnerFile, File modelFile) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        if ("voxtral4b-main".equals(runnerFile.getName())) {
            command.add(modelFile.getAbsolutePath());
            return command;
        }
        if (shouldInjectVoxtralBackend(runnerFile)) {
            command.add("--backend");
            command.add("voxtral4b");
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        command.add("-n");
        command.add("1");
        command.add("-p");
        command.add("ok");
        return command;
    }

    private boolean shouldInjectVoxtralBackend(File runnerFile) {
        if (runnerFile == null) return false;
        String name = String.valueOf(runnerFile.getName()).toLowerCase(Locale.US);
        return name.startsWith("crispasr");
    }

    private String resolveSystemLinkerPath() {
        File linker64 = new File("/system/bin/linker64");
        if (linker64.isFile()) return linker64.getAbsolutePath();
        File linker = new File("/system/bin/linker");
        if (linker.isFile()) return linker.getAbsolutePath();
        return null;
    }

    private String resolveRunnerBinaryPath(List<String> command) {
        if (command == null || command.isEmpty()) return null;
        String first = String.valueOf(command.get(0));
        if ("/system/bin/linker64".equals(first) || "/system/bin/linker".equals(first)) {
            if (command.size() >= 2) return String.valueOf(command.get(1));
            return null;
        }
        return first;
    }

    private String resolveNativeLibDir() {
        try {
            ApplicationInfo appInfo = getApplicationContext().getApplicationInfo();
            return appInfo != null ? String.valueOf(appInfo.nativeLibraryDir == null ? "" : appInfo.nativeLibraryDir) : "";
        } catch (Exception ignored) {
            return "";
        }
    }

    private String buildRunnerLibraryPath(File runnerDir, String nativeLibDir) {
        StringBuilder builder = new StringBuilder();
        if (runnerDir != null) {
            builder.append(runnerDir.getAbsolutePath());
        }
        if (nativeLibDir != null && !nativeLibDir.isBlank()) {
            if (builder.length() > 0) builder.append(':');
            builder.append(nativeLibDir);
        }
        return builder.toString();
    }

    private String extractTranscript(String output) {
        return output == null ? "" : output.trim();
    }

    private String joinShellArgs(List<String> args) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < args.size(); i++) {
            if (i > 0) builder.append(' ');
            builder.append(shQuote(args.get(i)));
        }
        return builder.toString();
    }

    private String shQuote(String input) {
        if (input == null) return "''";
        return "'" + input.replace("'", "'\"'\"'") + "'";
    }

    private boolean containsMissingAudioTensorError(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase(Locale.US);
        return normalized.contains("missing required audio tensors")
                || normalized.contains("required audio tensors are missing")
                || normalized.contains("missing audio tensors");
    }

    private boolean containsUnsupportedModelArchitecture(String text) {
        String normalized = String.valueOf(text == null ? "" : text).toLowerCase(Locale.US);
        return normalized.contains("unknown model architecture")
                || normalized.contains("unsupported model architecture")
                || normalized.contains("model architecture not supported");
    }

    private static final class ProcessOutput {
        final int exitCode;
        final String output;

        ProcessOutput(int exitCode, String output) {
            this.exitCode = exitCode;
            this.output = output == null ? "" : output;
        }
    }
}
