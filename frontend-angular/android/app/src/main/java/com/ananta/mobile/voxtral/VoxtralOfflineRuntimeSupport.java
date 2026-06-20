package com.ananta.mobile.voxtral;

import android.Manifest;
import android.app.ActivityManager;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.net.Uri;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Build;
import android.provider.Settings;
import android.os.StatFs;
import android.content.SharedPreferences;

import com.ananta.mobile.security.PermissionBroker;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

import org.json.JSONException;
import org.json.JSONObject;

abstract class VoxtralOfflineRuntimeSupport extends VoxtralOfflineCore {
    protected abstract void appendAudit(String decision, String action, String reason);
    protected abstract boolean isRunnerCandidate(String baseName);
    protected abstract String runnerLibraryPathExport(File runnerFile);

    protected List<String> buildRunnerCommand(File runnerFile, File modelFile, File audioFile) {
        return buildRunnerCommand(runnerFile, modelFile, audioFile, false);
    }

    protected List<String> buildRunnerCommand(File runnerFile, File modelFile, File audioFile, boolean lowMemoryMode) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            command.add(audioFile.getAbsolutePath());
            return command;
        }
        if (isVoxtralRealtimeRunner(runnerFile)) {
            command.add("--model");
            command.add(modelFile.getAbsolutePath());
            command.add("--audio");
            command.add(audioFile.getAbsolutePath());
            command.add("--threads");
            command.add(lowMemoryMode ? "2" : "4");
            command.add("--max-len");
            command.add(lowMemoryMode ? "96" : "256");
            command.add("--log-level");
            command.add("warn");
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

    protected List<String> buildRunnerProbeCommand(File runnerFile, File modelFile) {
        List<String> command = new ArrayList<>();
        command.add(runnerFile.getAbsolutePath());
        String binaryName = runnerFile.getName();
        if ("voxtral4b-main".equals(binaryName)) {
            command.add(modelFile.getAbsolutePath());
            return command;
        }
        if (shouldInjectVoxtralBackend(runnerFile)) {
            command.add("--backend");
            command.add("voxtral4b");
        }
        command.add("-m");
        command.add(modelFile.getAbsolutePath());
        return command;
    }

    protected boolean shouldInjectVoxtralBackend(File runnerFile) {
        if (runnerFile == null) return false;
        String name = String.valueOf(runnerFile.getName()).toLowerCase();
        return name.startsWith("crispasr");
    }

    protected boolean isVoxtralRealtimeRunner(File runnerFile) {
        if (runnerFile == null) return false;
        String name = String.valueOf(runnerFile.getName()).toLowerCase();
        return "voxtral-realtime".equals(name) || "voxtral-realtime-bin".equals(name) || "voxtral".equals(name);
    }

    protected RuntimeMemoryCheck evaluateRuntimeMemory(File modelFile) {
        return evaluateRuntimeMemory(modelFile, false);
    }

    protected RuntimeMemoryCheck evaluateRuntimeMemoryWithRetry(File modelFile, boolean lowMemoryMode) {
        RuntimeMemoryCheck firstCheck = evaluateRuntimeMemory(modelFile, lowMemoryMode);
        if (firstCheck.hasEnoughMemory) return firstCheck;

        // Try to reclaim Java heap before making a hard reject on tight-memory devices.
        Runtime runtime = Runtime.getRuntime();
        runtime.gc();
        runtime.runFinalization();
        System.gc();

        RuntimeMemoryCheck secondCheck = evaluateRuntimeMemory(modelFile, lowMemoryMode);
        if (secondCheck.availableBytes > firstCheck.availableBytes) return secondCheck;
        return firstCheck;
    }

    protected RuntimeMemoryCheck evaluateRuntimeMemory(File modelFile, boolean lowMemoryLiveMode) {
        ActivityManager activityManager = (ActivityManager) getContext().getSystemService(android.content.Context.ACTIVITY_SERVICE);
        if (activityManager == null) {
            return new RuntimeMemoryCheck(false, 0, DEFAULT_MIN_RUNTIME_FREE_BYTES);
        }
        ActivityManager.MemoryInfo memoryInfo = new ActivityManager.MemoryInfo();
        activityManager.getMemoryInfo(memoryInfo);
        long availableBytes = Math.max(0L, memoryInfo.availMem);
        long modelBytes = (modelFile != null && modelFile.exists()) ? Math.max(0L, modelFile.length()) : 0L;
        long estimatedRequiredBytes;
        long reserveBytes;
        if (lowMemoryLiveMode) {
            estimatedRequiredBytes = Math.max(
                    LOW_MEMORY_LIVE_MIN_RUNTIME_FREE_BYTES,
                    Math.max(
                            modelBytes + LOW_MEMORY_LIVE_HEADROOM_BYTES,
                            (modelBytes * LOW_MEMORY_LIVE_MULTIPLIER_NUM) / LOW_MEMORY_LIVE_MULTIPLIER_DEN
                    )
            );
            reserveBytes = LOW_MEMORY_LIVE_SAFETY_RESERVE_BYTES;
        } else {
            estimatedRequiredBytes = Math.max(
                    DEFAULT_MIN_RUNTIME_FREE_BYTES,
                    Math.max(
                            modelBytes + RUNTIME_MODEL_HEADROOM_BYTES,
                            (modelBytes * RUNTIME_MODEL_MULTIPLIER_NUM) / RUNTIME_MODEL_MULTIPLIER_DEN
                    )
            );
            reserveBytes = RUNTIME_SAFETY_RESERVE_BYTES;
        }
        long currentProcessPssBytes = readCurrentProcessPssBytes(activityManager);
        long guardedRequiredBytes = estimatedRequiredBytes + reserveBytes + Math.max(0L, currentProcessPssBytes / 2L);
        boolean hasEnoughMemory = !memoryInfo.lowMemory && availableBytes >= guardedRequiredBytes;
        return new RuntimeMemoryCheck(hasEnoughMemory, availableBytes, guardedRequiredBytes);
    }

    protected long readCurrentProcessPssBytes(ActivityManager activityManager) {
        if (activityManager == null) return 0L;
        try {
            android.os.Debug.MemoryInfo[] infos = activityManager.getProcessMemoryInfo(new int[]{android.os.Process.myPid()});
            if (infos == null || infos.length == 0 || infos[0] == null) return 0L;
            return Math.max(0L, infos[0].getTotalPss()) * 1024L;
        } catch (Exception ignored) {
            return 0L;
        }
    }

    protected String formatBytes(long bytes) {
        if (bytes <= 0) return "0 B";
        String[] units = new String[]{"B", "KB", "MB", "GB", "TB"};
        double value = bytes;
        int index = 0;
        while (value >= 1024 && index < units.length - 1) {
            value /= 1024d;
            index += 1;
        }
        return String.format(java.util.Locale.US, index < 2 ? "%.0f %s" : "%.1f %s", value, units[index]);
    }

    protected void resetLiveSessionState(boolean deleteBufferedAudio) {
        File buffered;
        synchronized (recordingLock) {
            buffered = liveBufferedWavFile;
            liveLowMemoryMode = false;
            liveSessionSampleRate = 16000;
            liveBufferedWavFile = null;
            liveBufferedPcmBytes = 0;
            liveSessionModelFile = null;
            liveSessionRunnerFile = null;
        }
        if (deleteBufferedAudio && buffered != null && buffered.exists()) {
            buffered.delete();
        }
    }

    protected String joinShellArgs(List<String> args) {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < args.size(); i++) {
            if (i > 0) builder.append(' ');
            builder.append(shQuote(args.get(i)));
        }
        return builder.toString();
    }

    protected String extractTranscript(String output) {
        if (output == null) return "";
        return output.trim();
    }

    protected String readAllLimited(InputStream stream, int maxChars) throws IOException {
        StringBuilder builder = new StringBuilder(Math.min(maxChars, 8192));
        long droppedChars = 0;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[2048];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                int remaining = maxChars - builder.length();
                if (remaining > 0) {
                    int toAppend = Math.min(remaining, read);
                    builder.append(buffer, 0, toAppend);
                    droppedChars += (read - toAppend);
                } else {
                    droppedChars += read;
                }
            }
        }
        if (droppedChars > 0) {
            builder
                .append("\n...[output truncated: ")
                .append(droppedChars)
                .append(" chars omitted]");
        }
        return builder.toString();
    }

    protected boolean isValidWavAudio(File audioFile) {
        if (audioFile == null || !audioFile.exists() || audioFile.length() <= 44) return false;
        try (RandomAccessFile input = new RandomAccessFile(audioFile, "r")) {
            byte[] header = new byte[12];
            input.readFully(header);
            String riff = new String(header, 0, 4, StandardCharsets.US_ASCII);
            String wave = new String(header, 8, 4, StandardCharsets.US_ASCII);
            return "RIFF".equals(riff) && "WAVE".equals(wave);
        } catch (IOException error) {
            return false;
        }
    }

    protected File ensureDir(String subPath) {
        File dir = new File(getContext().getFilesDir(), subPath);
        if (dir.exists()) return dir;
        if (dir.mkdirs()) return dir;
        return null;
    }

    protected JSArray listFiles(File dir, String extensionFilter) {
        JSArray files = new JSArray();
        if (dir == null || !dir.exists() || !dir.isDirectory()) {
            return files;
        }
        File[] list = dir.listFiles();
        if (list == null) return files;
        for (File file : list) {
            if (!file.isFile()) continue;
            if (extensionFilter != null && !file.getName().toLowerCase().endsWith(extensionFilter)) continue;
            JSObject item = new JSObject();
            item.put("name", file.getName());
            item.put("path", file.getAbsolutePath());
            item.put("bytes", file.length());
            item.put("executable", file.canExecute());
            files.put(item);
        }
        return files;
    }

    protected String computeSha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream fis = new FileInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = fis.read(buffer)) > 0) {
                digest.update(buffer, 0, read);
            }
        }
        byte[] hash = digest.digest();
        StringBuilder hex = new StringBuilder(hash.length * 2);
        for (byte b : hash) {
            String part = Integer.toHexString(0xff & b);
            if (part.length() == 1) hex.append('0');
            hex.append(part);
        }
        return hex.toString();
    }

    protected void restoreSelectionState() {
        if (lastModelPath == null || lastModelPath.isBlank() || !new File(lastModelPath).isFile()) {
            String persistedModel = prefs().getString(PREF_MODEL_PATH, "");
            if (persistedModel != null && !persistedModel.isBlank() && new File(persistedModel).isFile()) {
                lastModelPath = persistedModel;
            } else {
                File model = selectDefaultAsset(ensureDir("voxtral/models"), MODEL_EXTENSION);
                lastModelPath = model != null ? model.getAbsolutePath() : null;
                if (lastModelPath != null) persistSelection(PREF_MODEL_PATH, lastModelPath);
            }
        }
        if (lastRunnerPath == null || lastRunnerPath.isBlank() || !new File(lastRunnerPath).isFile()) {
            String persistedRunner = prefs().getString(PREF_RUNNER_PATH, "");
            if (persistedRunner != null && !persistedRunner.isBlank() && new File(persistedRunner).isFile()) {
                lastRunnerPath = persistedRunner;
            } else {
                File runner = selectDefaultRunner(ensureDir("voxtral/bin"));
                lastRunnerPath = runner != null ? runner.getAbsolutePath() : null;
                if (lastRunnerPath != null) persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
            }
        }
    }

    protected SharedPreferences prefs() {
        return getContext().getSharedPreferences(PREFS_NAME, 0);
    }

    protected void persistSelection(String key, String value) {
        prefs().edit().putString(key, String.valueOf(value == null ? "" : value)).apply();
    }

    protected String readSelection(String key) {
        return prefs().getString(key, "");
    }

    protected void installBundledVoxtralRunnerIfAvailable() {
        try {
            String[] assets = getContext().getAssets().list(BUNDLED_VOXTRAL_RUNNER_ASSET_DIR);
            if (assets == null || assets.length == 0) return;
            boolean hasRunner = false;
            for (String asset : assets) {
                if (BUNDLED_VOXTRAL_RUNNER_FILE.equals(asset)) {
                    hasRunner = true;
                    break;
                }
            }
            if (!hasRunner) return;

            File runnerDir = ensureDir("voxtral/bin");
            if (runnerDir == null) {
                appendAudit("deny", "voxtral_runner_asset", "missing_runner_directory");
                return;
            }
            for (String asset : assets) {
                if (asset == null || asset.isBlank()) continue;
                copyAssetToFile(BUNDLED_VOXTRAL_RUNNER_ASSET_DIR + "/" + asset, new File(runnerDir, asset));
            }

            File runner = new File(runnerDir, BUNDLED_VOXTRAL_RUNNER_FILE);
            if (runner.isFile()) {
                runner.setReadable(true, false);
                runner.setWritable(true, true);
                runner.setExecutable(true, false);
                lastRunnerPath = runner.getAbsolutePath();
                persistSelection(PREF_RUNNER_PATH, lastRunnerPath);
            }
        } catch (IOException error) {
            appendAudit("deny", "voxtral_runner_asset", "install_failed " + error.getMessage());
        }
    }

    protected void copyAssetToFile(String assetPath, File target) throws IOException {
        try (InputStream input = new BufferedInputStream(getContext().getAssets().open(assetPath));
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.flush();
        }
        target.setReadable(true, false);
        target.setWritable(true, true);
        if (BUNDLED_VOXTRAL_RUNNER_FILE.equals(target.getName())) {
            target.setExecutable(true, false);
        }
    }

    protected File selectDefaultAsset(File dir, String extensionFilter) {
        if (dir == null || !dir.isDirectory()) return null;
        File[] files = dir.listFiles();
        if (files == null) return null;
        File best = null;
        for (File file : files) {
            if (file == null || !file.isFile()) continue;
            if (extensionFilter != null && !file.getName().toLowerCase().endsWith(extensionFilter)) continue;
            if (best == null || file.lastModified() > best.lastModified()) best = file;
        }
        return best;
    }

    protected File selectDefaultRunner(File dir) {
        if (dir == null || !dir.isDirectory()) return null;
        File[] files = dir.listFiles();
        if (files == null) return null;
        File bestPreferred = null;
        File bestFallback = null;
        for (File file : files) {
            if (file == null || !file.isFile()) continue;
            if (!isRunnerCandidate(file.getName())) continue;
            if (isPreferredVoxtralRunner(file.getName())) {
                if (bestPreferred == null || file.lastModified() > bestPreferred.lastModified()) {
                    bestPreferred = file;
                }
            } else if (!isKnownPlainLlamaRunner(file.getName())) {
                if (bestFallback == null || file.lastModified() > bestFallback.lastModified()) {
                    bestFallback = file;
                }
            }
        }
        return bestPreferred != null ? bestPreferred : bestFallback;
    }

    protected boolean isPreferredVoxtralRunner(String baseName) {
        if (baseName == null) return false;
        String name = baseName.toLowerCase();
        return name.contains("voxtral") || name.startsWith("crispasr");
    }

    protected boolean isKnownPlainLlamaRunner(String baseName) {
        if (baseName == null) return false;
        String name = baseName.toLowerCase();
        return name.equals("llama-cli") || name.equals("llama-server");
    }

    protected File prepareRunnerForExecution(File sourceRunner) throws IOException {
        if (!sourceRunner.exists()) {
            throw new IOException("Runner file not found: " + sourceRunner.getAbsolutePath());
        }
        sourceRunner.setReadable(true, false);
        sourceRunner.setWritable(true, true);
        sourceRunner.setExecutable(true, false);
        if (sourceRunner.canExecute()) {
            return sourceRunner;
        }
        List<File> candidateDirs = buildExecDirCandidates(sourceRunner);
        IOException lastError = null;

        for (File candidateDir : candidateDirs) {
            File execDir = ensureDir(candidateDir);
            if (execDir == null) continue;
            File stagedRunner = new File(execDir, sourceRunner.getName());
            try {
                boolean needsCopy = !stagedRunner.exists()
                        || stagedRunner.length() != sourceRunner.length()
                        || stagedRunner.lastModified() < sourceRunner.lastModified();
                if (needsCopy) {
                    copyFile(sourceRunner, stagedRunner);
                    stagedRunner.setLastModified(sourceRunner.lastModified());
                }

                stagedRunner.setReadable(true, false);
                stagedRunner.setWritable(true, true);
                stagedRunner.setExecutable(true, false);
                if (!stagedRunner.canExecute()) {
                    throw new IOException("Runner is not executable after staging: " + stagedRunner.getAbsolutePath());
                }
                return stagedRunner;
            } catch (IOException error) {
                lastError = error;
            }
        }
        if (lastError != null) {
            throw lastError;
        }
        throw new IOException("Could not stage runner in an executable directory.");
    }

    protected List<File> buildExecDirCandidates(File sourceRunner) {
        List<File> dirs = new ArrayList<>();
        if (sourceRunner != null && sourceRunner.getParentFile() != null) {
            dirs.add(sourceRunner.getParentFile());
        }
        dirs.add(new File(getContext().getFilesDir(), "voxtral/exec"));
        dirs.add(new File(getContext().getNoBackupFilesDir(), "voxtral/exec"));
        dirs.add(new File(getContext().getCacheDir(), "voxtral/exec"));
        return dirs;
    }

    protected File ensureDir(File dir) {
        if (dir == null) return null;
        if (dir.exists()) return dir;
        if (dir.mkdirs()) return dir;
        return null;
    }

    protected void copyFile(File source, File target) throws IOException {
        try (FileInputStream input = new FileInputStream(source);
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.flush();
        }
    }

    protected boolean canSpawnRunner(File runnerFile) {
        Process process = null;
        try {
            process = new ProcessBuilder(runnerFile.getAbsolutePath())
                    .redirectErrorStream(true)
                    .start();
            process.waitFor(1, TimeUnit.SECONDS);
            return true;
        } catch (Exception ignored) {
            return false;
        } finally {
            if (process != null && process.isAlive()) {
                process.destroy();
            }
        }
    }

    protected boolean canSpawnRunnerViaProot(File runnerFile) {
        Process process = null;
        try {
            File prootWrapper = resolveProotWrapper();
            File ubuntuRootfs = resolveUbuntuRootfs();
            if (prootWrapper == null || ubuntuRootfs == null || runnerFile == null || !runnerFile.exists()) return false;
            String runtimePath = new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR).getAbsolutePath();
            String rootfsPath = ubuntuRootfs.getAbsolutePath();
            String nativeLibDir = resolveNativeLibDir();
            String prootLoaderSrc = resolveNativeLibPath("libproot-loader.so");
            String prootLoaderLink = new File(getContext().getDataDir(), "ldr/libproot-loader.so").getAbsolutePath();
            String wrappedInnerCommand = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                + "export HOME=/root; export TERM=xterm-256color; "
                + runnerLibraryPathExport(runnerFile)
                + shQuote(runnerFile.getAbsolutePath()) + " --help >/dev/null 2>&1";
            String cmd = ""
                + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
                + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
                + "ANANTA_PROOT_WRAPPER=" + shQuote(prootWrapper.getAbsolutePath()) + "; "
                + "ANANTA_LIB_DIR=" + shQuote(nativeLibDir == null ? "" : nativeLibDir) + "; "
                + "ANANTA_PROOT_LOADER_SRC=" + shQuote(prootLoaderSrc == null ? "" : prootLoaderSrc) + "; "
                + "ANANTA_PROOT_LOADER_LINK=" + shQuote(prootLoaderLink) + "; "
                + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
                + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
                + "chmod 755 \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null || true; "
                + "if [ -n \"$ANANTA_PROOT_LOADER_SRC\" ]; then "
                + "mkdir -p \"$(dirname \"$ANANTA_PROOT_LOADER_LINK\")\" 2>/dev/null || true; "
                + "ln -sfn \"$ANANTA_PROOT_LOADER_SRC\" \"$ANANTA_PROOT_LOADER_LINK\" 2>/dev/null || true; "
                + "fi; "
                + "PROOT_LOADER=\"$ANANTA_PROOT_LOADER_LINK\" PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
                + "LD_LIBRARY_PATH=\"$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}\" "
                + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
                + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
                + "-w / /bin/sh -c "
                + shQuote(wrappedInnerCommand);
            process = new ProcessBuilder("/system/bin/sh", "-lc", cmd)
                .redirectErrorStream(true)
                .start();
            return process.waitFor(3, TimeUnit.SECONDS);
        } catch (Exception ignored) {
            return false;
        } finally {
            if (process != null && process.isAlive()) process.destroy();
        }
    }

    protected String runShellCommandViaProot(String wrappedInnerCommand) throws IOException, InterruptedException {
        File prootWrapper = resolveProotWrapper();
        File ubuntuRootfs = resolveUbuntuRootfs();
        if (prootWrapper == null || ubuntuRootfs == null) {
            throw new IOException("Proot runtime is unavailable.");
        }
        String runtimePath = new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR).getAbsolutePath();
        String rootfsPath = ubuntuRootfs.getAbsolutePath();
        String nativeLibDir = resolveNativeLibDir();
        String prootLoaderSrc = resolveNativeLibPath("libproot-loader.so");
        String prootLoaderLink = new File(getContext().getDataDir(), "ldr/libproot-loader.so").getAbsolutePath();
        String shellCommand = ""
            + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
            + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
            + "ANANTA_PROOT_WRAPPER=" + shQuote(prootWrapper.getAbsolutePath()) + "; "
            + "ANANTA_LIB_DIR=" + shQuote(nativeLibDir == null ? "" : nativeLibDir) + "; "
            + "ANANTA_PROOT_LOADER_SRC=" + shQuote(prootLoaderSrc == null ? "" : prootLoaderSrc) + "; "
            + "ANANTA_PROOT_LOADER_LINK=" + shQuote(prootLoaderLink) + "; "
            + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
            + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 755 \"$ANANTA_PROOT_WRAPPER\" 2>/dev/null || true; "
            + "if [ -n \"$ANANTA_PROOT_LOADER_SRC\" ]; then "
            + "mkdir -p \"$(dirname \"$ANANTA_PROOT_LOADER_LINK\")\" 2>/dev/null || true; "
            + "ln -sfn \"$ANANTA_PROOT_LOADER_SRC\" \"$ANANTA_PROOT_LOADER_LINK\" 2>/dev/null || true; "
            + "fi; "
            + "PROOT_LOADER=\"$ANANTA_PROOT_LOADER_LINK\" PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
            + "LD_LIBRARY_PATH=\"$ANANTA_LIB_DIR:${LD_LIBRARY_PATH:-}\" "
            + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
            + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
            + "-0 -r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
            + "-w / /bin/sh -c "
            + shQuote(wrappedInnerCommand);
        Process process = new ProcessBuilder("/system/bin/sh", "-lc", shellCommand)
            .redirectErrorStream(true)
            .start();
        String output = readAllLimited(process.getInputStream(), MAX_PROCESS_OUTPUT_CHARS);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IOException("Proot command failed with exit code " + exitCode + "\n" + output);
        }
        return output;
    }

    protected File resolveProotWrapper() {
        File runtimeProot = new File(new File(new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR), "bin"), "proot");
        String nativeProotPath = resolveNativeLibPath("libprootclassic.so");
        if (nativeProotPath != null && !nativeProotPath.isBlank()) {
            File parent = runtimeProot.getParentFile();
            if (parent != null && !parent.isDirectory()) {
                parent.mkdirs();
            }
            ensureWrapperTargets(runtimeProot, nativeProotPath);
            if (runtimeProot.canRead() && runtimeProot.canExecute()) return runtimeProot;
        }
        if (runtimeProot.isFile()) {
            runtimeProot.setReadable(true, false);
            runtimeProot.setExecutable(true, false);
            if (runtimeProot.canRead() && runtimeProot.canExecute()) return runtimeProot;
        }
        if (nativeProotPath != null && !nativeProotPath.isBlank()) {
            File direct = new File(nativeProotPath);
            if (direct.isFile() && direct.canExecute()) return direct;
        }
        return null;
    }

    protected File resolveUbuntuRootfs() {
        File rootfsDir = new File(new File(new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR), "distros/ubuntu"), "rootfs");
        if (!rootfsDir.isDirectory()) return null;
        if (new File(rootfsDir, "bin/sh").isFile() || new File(rootfsDir, "usr/bin/sh").isFile()) return rootfsDir;
        File[] children = rootfsDir.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (!child.isDirectory()) continue;
            if (new File(child, "bin/sh").isFile() || new File(child, "usr/bin/sh").isFile()) return child;
        }
        return null;
    }

    protected File resolveRuntimeLlamaCli() {
        File llamaCli = new File(new File(new File(getContext().getFilesDir(), LLM_RUNTIME_SUBDIR), "llama-cpp"), "llama-cli");
        return llamaCli.isFile() ? llamaCli : null;
    }

    protected String shQuote(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return "'" + text.replace("'", "'\"'\"'") + "'";
    }

    protected void ensureWrapperTargets(File wrapperFile, String targetBinaryPath) {
        if (wrapperFile == null || targetBinaryPath == null || targetBinaryPath.isBlank()) return;
        try {
            String script = "#!/system/bin/sh\nexec \"" + targetBinaryPath + "\" \"$@\"\n";
            byte[] bytes = script.getBytes(StandardCharsets.UTF_8);
            boolean needsRewrite = true;
            if (wrapperFile.isFile() && wrapperFile.length() == bytes.length) {
                try (FileInputStream input = new FileInputStream(wrapperFile)) {
                    byte[] existing = new byte[bytes.length];
                    int read = input.read(existing);
                    if (read == bytes.length && Arrays.equals(existing, bytes)) {
                        needsRewrite = false;
                    }
                }
            }
            if (needsRewrite) {
                try (FileOutputStream out = new FileOutputStream(wrapperFile, false)) {
                    out.write(bytes);
                    out.flush();
                }
            }
            wrapperFile.setReadable(true, false);
            wrapperFile.setWritable(true, true);
            wrapperFile.setExecutable(true, false);
        } catch (Exception ignored) {
            // Best effort only.
        }
    }

    protected String resolveNativeLibPath(String libName) {
        ApplicationInfo appInfo = getContext().getApplicationInfo();
        if (appInfo == null) return null;
        if (appInfo.nativeLibraryDir != null) {
            File lib = new File(appInfo.nativeLibraryDir, libName);
            if (lib.isFile()) return lib.getAbsolutePath();
        }

        // Fallback path derivation when nativeLibraryDir is stale/unavailable.
        String sourceDir = appInfo.sourceDir;
        if (sourceDir != null && !sourceDir.isBlank()) {
            File apkDir = new File(sourceDir).getParentFile();
            if (apkDir != null) {
                String abi = Build.SUPPORTED_ABIS != null && Build.SUPPORTED_ABIS.length > 0
                    ? String.valueOf(Build.SUPPORTED_ABIS[0])
                    : "";
                String libSubdir = abi.startsWith("arm64") ? "arm64"
                    : abi.startsWith("armeabi") ? "arm"
                    : abi.startsWith("x86_64") ? "x86_64"
                    : abi.startsWith("x86") ? "x86" : "arm64";
                File derived = new File(new File(new File(apkDir, "lib"), libSubdir), libName);
                if (derived.isFile()) return derived.getAbsolutePath();
            }
        }
        return null;
    }

    protected String resolveNativeLibDir() {
        ApplicationInfo appInfo = getContext().getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        return appInfo.nativeLibraryDir;
    }

    protected String inferFileNameFromUrl(String rawUrl) {
        String trimmed = rawUrl.trim();
        int queryIndex = trimmed.indexOf('?');
        String clean = queryIndex >= 0 ? trimmed.substring(0, queryIndex) : trimmed;
        int slashIndex = clean.lastIndexOf('/');
        if (slashIndex >= 0 && slashIndex < clean.length() - 1) {
            return clean.substring(slashIndex + 1);
        }
        return "download_" + System.currentTimeMillis();
    }

    protected void writeWavHeader(FileOutputStream out, int sampleRate, int channels, int bitsPerSample, int dataLength) throws IOException {
        byte[] header = new byte[44];
        int byteRate = sampleRate * channels * bitsPerSample / 8;
        int blockAlign = channels * bitsPerSample / 8;
        int totalDataLen = dataLength + 36;

        header[0] = 'R'; header[1] = 'I'; header[2] = 'F'; header[3] = 'F';
        header[4] = (byte) (totalDataLen & 0xff);
        header[5] = (byte) ((totalDataLen >> 8) & 0xff);
        header[6] = (byte) ((totalDataLen >> 16) & 0xff);
        header[7] = (byte) ((totalDataLen >> 24) & 0xff);
        header[8] = 'W'; header[9] = 'A'; header[10] = 'V'; header[11] = 'E';
        header[12] = 'f'; header[13] = 'm'; header[14] = 't'; header[15] = ' ';
        header[16] = 16; header[17] = 0; header[18] = 0; header[19] = 0;
        header[20] = 1; header[21] = 0;
        header[22] = (byte) channels; header[23] = 0;
        header[24] = (byte) (sampleRate & 0xff);
        header[25] = (byte) ((sampleRate >> 8) & 0xff);
        header[26] = (byte) ((sampleRate >> 16) & 0xff);
        header[27] = (byte) ((sampleRate >> 24) & 0xff);
        header[28] = (byte) (byteRate & 0xff);
        header[29] = (byte) ((byteRate >> 8) & 0xff);
        header[30] = (byte) ((byteRate >> 16) & 0xff);
        header[31] = (byte) ((byteRate >> 24) & 0xff);
        header[32] = (byte) blockAlign; header[33] = 0;
        header[34] = (byte) bitsPerSample; header[35] = 0;
        header[36] = 'd'; header[37] = 'a'; header[38] = 't'; header[39] = 'a';
        header[40] = (byte) (dataLength & 0xff);
        header[41] = (byte) ((dataLength >> 8) & 0xff);
        header[42] = (byte) ((dataLength >> 16) & 0xff);
        header[43] = (byte) ((dataLength >> 24) & 0xff);

        out.write(header, 0, 44);
    }

    protected void updateWavHeader(File wavFile, int sampleRate, int channels, int bitsPerSample, int dataLength) throws IOException {
        try (RandomAccessFile raf = new RandomAccessFile(wavFile, "rw")) {
            int byteRate = sampleRate * channels * bitsPerSample / 8;
            int totalDataLen = dataLength + 36;
            raf.seek(4);
            raf.write(intToLittleEndian(totalDataLen));
            raf.seek(24);
            raf.write(intToLittleEndian(sampleRate));
            raf.seek(28);
            raf.write(intToLittleEndian(byteRate));
            raf.seek(40);
            raf.write(intToLittleEndian(dataLength));
        }
    }

    protected byte[] intToLittleEndian(int value) {
        return new byte[]{
                (byte) (value & 0xff),
                (byte) ((value >> 8) & 0xff),
                (byte) ((value >> 16) & 0xff),
                (byte) ((value >> 24) & 0xff)
        };
    }
}
