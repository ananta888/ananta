package com.ananta.mobile.llm;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.util.Log;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

/**
 * Manages the llama.cpp server lifecycle: download, install, start, stop, health check.
 * The llama-server binary is a Ubuntu ARM64 ELF and must run inside proot.
 *
 * <p>Follows SRP: this class owns LLM server management only.
 * Download utilities are delegated to {@link DownloadHelper}.
 * Proot invocation details are encapsulated in {@link #buildProotServerCommand}.</p>
 */
public class LlmServerManager {

    private static final String TAG = "LlmServerManager";

    // Version-pinned artifacts with SHA-256 checksums
    private static final String LLAMA_VERSION = "b8994";
    private static final String LLAMA_TARBALL_URL =
        "https://github.com/ggml-org/llama.cpp/releases/download/" + LLAMA_VERSION
            + "/llama-" + LLAMA_VERSION + "-bin-ubuntu-arm64.tar.gz";
    private static final String LLAMA_TARBALL_SHA256 =
        "50e857be7a77a2a591550834a590f01b8189f5fa6f84290db749a371e4d61287";
    private static final String BUNDLED_LLAMA_TARBALL_ASSET =
        "llm-server/llama-" + LLAMA_VERSION + "-bin-ubuntu-arm64.tar.gz";

    private static final String MODEL_NAME = "SmolLM2-135M-Instruct-Q8_0.gguf";
    private static final String MODEL_URL =
        "https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/main/" + MODEL_NAME;
    private static final String MODEL_SHA256 =
        "5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83";

    private static final int SERVER_PORT = 8081;
    private static final int CONTEXT_SIZE = 16384;

    private static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    private static final String LLM_RUNTIME_SUBDIR = "llm-runtime";
    private static final String LLAMA_DIR = "llama-cpp";
    private static final String MODELS_DIR = "models";
    private static final String DOWNLOADS_DIR = "downloads";
    private static final String ACTIVE_MODEL_MARKER = ".active-model";

    /** Possible states for the manager. */
    public enum State {
        IDLE, INSTALLING_SERVER, INSTALLING_MODEL, STARTING, RUNNING, STOPPING, ERROR
    }

    private final Context context;
    private volatile Process serverProcess;
    private volatile State state = State.IDLE;
    private volatile String lastError;
    private Thread stdoutDrainer;

    public LlmServerManager(Context context) {
        this.context = context;
    }

    // ── Status ──────────────────────────────────────────────────────────

    public State getState() { return state; }
    public String getLastError() { return lastError; }

    public boolean isServerInstalled() {
        File server = new File(llamaCppDir(), "llama-server");
        File versionMarker = new File(llamaCppDir(), ".version-" + LLAMA_VERSION);
        return server.exists() && server.canRead() && versionMarker.exists();
    }

    public boolean isModelInstalled() {
        return getActiveModelFile() != null;
    }

    public boolean isServerRunning() {
        Process proc = serverProcess;
        return proc != null && proc.isAlive();
    }

    public boolean isProotReady() {
        File rootfs = resolveUbuntuRootfs();
        File prootBin = resolveProotBinary();
        return rootfs != null && prootBin != null;
    }

    public LlmSetupStatus getFullStatus() {
        tryAutoInstallBundledServer();
        return new LlmSetupStatus(
            isProotReady(),
            isServerInstalled(),
            isModelInstalled(),
            isServerRunning(),
            state.name(),
            lastError,
            LLAMA_VERSION,
            getActiveModelName(),
            SERVER_PORT
        );
    }

    // ── Install llama.cpp server ────────────────────────────────────────

    public void installLlamaServer(DownloadHelper.ProgressListener listener) throws Exception {
        if (state == State.INSTALLING_SERVER || state == State.INSTALLING_MODEL) {
            throw new IllegalStateException("Installation already in progress.");
        }
        state = State.INSTALLING_SERVER;
        lastError = null;
        try {
            File llamaDir = DownloadHelper.ensureDirectory(llamaCppDir());
            File versionMarker = new File(llamaDir, ".version-" + LLAMA_VERSION);
            File serverBin = new File(llamaDir, "llama-server");

            if (versionMarker.exists() && serverBin.exists() && serverBin.canRead()) {
                if (listener != null) {
                    listener.onProgress("done", "llama.cpp bereits installiert.", -1, -1);
                }
                state = State.IDLE;
                return;
            }

            if (installBundledServerIfAvailable(listener)) {
                state = State.IDLE;
                return;
            }

            File downloadsDir = DownloadHelper.ensureDirectory(downloadsDir());
            File tarball = new File(downloadsDir, "llama-" + LLAMA_VERSION + "-bin-ubuntu-arm64.tar.gz");

            if (!tarball.exists() || tarball.length() == 0) {
                DownloadHelper.downloadAtomically(LLAMA_TARBALL_URL, tarball, listener);
            }

            if (listener != null) {
                listener.onProgress("verifying", "SHA256 wird geprueft...", -1, -1);
            }
            DownloadHelper.verifySha256(tarball, LLAMA_TARBALL_SHA256);

            if (listener != null) {
                listener.onProgress("extracting", "Wird entpackt...", -1, -1);
            }
            extractTarGzToDirectory(tarball, llamaDir);

            // Mark executables
            for (String bin : new String[]{"llama-server", "llama-cli"}) {
                File f = new File(llamaDir, bin);
                if (f.exists()) f.setExecutable(true, false);
            }

            // Write version marker
            new FileOutputStream(versionMarker).close();

            // Clean up tarball to save space
            tarball.delete();

            if (listener != null) {
                listener.onProgress("done", "llama.cpp installiert.", -1, -1);
            }
            state = State.IDLE;
        } catch (Exception e) {
            state = State.ERROR;
            lastError = e.getMessage();
            throw e;
        }
    }

    // ── Install model ───────────────────────────────────────────────────

    public void installModel(DownloadHelper.ProgressListener listener) throws Exception {
        installModel(null, null, null, listener);
    }

    public void installModel(
        String requestedModelName,
        String requestedModelUrl,
        String requestedModelSha256,
        DownloadHelper.ProgressListener listener
    ) throws Exception {
        if (state == State.INSTALLING_SERVER || state == State.INSTALLING_MODEL) {
            throw new IllegalStateException("Installation already in progress.");
        }
        state = State.INSTALLING_MODEL;
        lastError = null;
        try {
            File modelsDir = DownloadHelper.ensureDirectory(modelsDir());
            String modelUrl = normalizeModelUrl(requestedModelUrl);
            String modelName = normalizeModelName(requestedModelName, modelUrl);
            String modelSha256 = normalizeModelSha256(requestedModelSha256, modelName, modelUrl);
            File model = new File(modelsDir, modelName);

            if (model.exists() && model.length() > 0) {
                if (modelSha256 != null) {
                    try {
                        DownloadHelper.verifySha256(model, modelSha256);
                        setActiveModel(modelName);
                        if (listener != null) {
                            listener.onProgress("done", "Modell bereits vorhanden.", -1, -1);
                        }
                        state = State.IDLE;
                        return;
                    } catch (Exception e) {
                        Log.w(TAG, "Existing model has wrong checksum, re-downloading.");
                        model.delete();
                    }
                } else {
                    setActiveModel(modelName);
                    if (listener != null) {
                        listener.onProgress("done", "Modell bereits vorhanden.", -1, -1);
                    }
                    state = State.IDLE;
                    return;
                }
            }

            DownloadHelper.downloadAtomically(modelUrl, model, listener);

            if (listener != null && modelSha256 != null) {
                listener.onProgress("verifying", "SHA256 wird geprueft...", -1, -1);
            }
            if (modelSha256 != null) {
                DownloadHelper.verifySha256(model, modelSha256);
            }
            setActiveModel(modelName);

            if (listener != null) {
                listener.onProgress("done", "Modell installiert.", -1, -1);
            }
            state = State.IDLE;
        } catch (Exception e) {
            state = State.ERROR;
            lastError = e.getMessage();
            throw e;
        }
    }

    // ── Start server ────────────────────────────────────────────────────

    public void startServer() throws Exception {
        if (isServerRunning()) {
            return;
        }
        if (!isServerInstalled()) {
            throw new IllegalStateException("llama.cpp ist nicht installiert.");
        }
        if (!isModelInstalled()) {
            throw new IllegalStateException("Kein Modell installiert.");
        }

        // Check if port is already in use
        if (isPortInUse()) {
            throw new IllegalStateException("Port " + SERVER_PORT + " ist bereits belegt.");
        }

        File prootBin = resolveProotBinary();
        File rootfs = resolveUbuntuRootfs();
        if (prootBin == null || rootfs == null) {
            throw new IllegalStateException("Proot runtime oder Ubuntu rootfs nicht gefunden.");
        }

        state = State.STARTING;
        lastError = null;
        try {
            String[] command = buildProotServerCommand(prootBin, rootfs);

            ProcessBuilder pb = new ProcessBuilder(command);
            pb.redirectErrorStream(true);
            applyProotEnvironment(pb, prootBin);

            serverProcess = pb.start();

            // Drain stdout/stderr to prevent pipe blocking
            final Process proc = serverProcess;
            stdoutDrainer = new Thread(() -> {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        Log.d(TAG, "llama-server: " + line);
                    }
                } catch (IOException ignored) {}
            }, "llama-server-stdout");
            stdoutDrainer.setDaemon(true);
            stdoutDrainer.start();

            // Wait for server to become responsive (up to 15 seconds)
            boolean ready = false;
            for (int attempt = 0; attempt < 30; attempt++) {
                Thread.sleep(500);
                if (!proc.isAlive()) {
                    throw new IOException("llama-server wurde unerwartet beendet.");
                }
                if (checkHealthQuiet()) {
                    ready = true;
                    break;
                }
            }

            if (!ready) {
                proc.destroyForcibly();
                serverProcess = null;
                throw new IOException("llama-server antwortet nicht nach 15 Sekunden.");
            }

            state = State.RUNNING;
            Log.i(TAG, "llama-server started on port " + SERVER_PORT);
        } catch (Exception e) {
            state = State.ERROR;
            lastError = e.getMessage();
            throw e;
        }
    }

    // ── Stop server ─────────────────────────────────────────────────────

    public void stopServer() {
        state = State.STOPPING;
        Process proc = serverProcess;
        if (proc != null) {
            proc.destroy();
            try {
                if (!proc.waitFor(5, TimeUnit.SECONDS)) {
                    proc.destroyForcibly();
                    proc.waitFor(2, TimeUnit.SECONDS);
                }
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        serverProcess = null;
        if (stdoutDrainer != null) {
            stdoutDrainer.interrupt();
            stdoutDrainer = null;
        }
        state = State.IDLE;
        Log.i(TAG, "llama-server stopped.");
    }

    // ── Health check ────────────────────────────────────────────────────

    public String checkHealth() throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL("http://127.0.0.1:" + SERVER_PORT + "/health");
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(3_000);
            connection.setReadTimeout(5_000);
            int status = connection.getResponseCode();
            if (status == 200) {
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8))) {
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    return sb.toString();
                }
            }
            throw new IOException("Health check returned HTTP " + status);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private boolean checkHealthQuiet() {
        try {
            checkHealth();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private boolean isPortInUse() {
        return checkHealthQuiet();
    }

    // ── Proot command construction ──────────────────────────────────────

    private String[] buildProotServerCommand(File prootBin, File rootfs) {
        String llamaDir = llamaCppDir().getAbsolutePath();
        File selectedModel = getActiveModelFile();
        if (selectedModel == null) {
            throw new IllegalStateException("Kein installierbares Modell gefunden.");
        }
        String modelPath = selectedModel.getAbsolutePath();

        File prootTmp = new File(prootRuntimeDir(), "tmp");
        if (!prootTmp.exists()) prootTmp.mkdirs();

        // Use exec to replace the shell process with llama-server
        // so that Process.destroy() kills the actual server
        String serverCmd = "export LD_LIBRARY_PATH='" + llamaDir + "':${LD_LIBRARY_PATH:-}"
            + " && exec '" + llamaDir + "/llama-server'"
            + " -m '" + modelPath + "'"
            + " --host 127.0.0.1"
            + " --port " + SERVER_PORT
            + " -c " + CONTEXT_SIZE
            + " -np 1"
            + " -ngl 0"
            + " --override-kv 'llama.context_length=int:" + CONTEXT_SIZE + "'";

        return new String[]{
            prootBin.getAbsolutePath(),
            "-r", rootfs.getAbsolutePath(),
            "-b", "/dev:/dev",
            "-b", "/proc:/proc",
            "-b", "/sys:/sys",
            "-b", "/data:/data",
            "-b", prootTmp.getAbsolutePath() + ":/tmp",
            "-w", "/root",
            "/bin/sh", "-c", serverCmd
        };
    }

    private void applyProotEnvironment(ProcessBuilder pb, File prootBin) {
        var env = pb.environment();
        // Clear Python env vars that could leak from Chaquopy
        env.remove("PYTHONPATH");
        env.remove("PYTHONHOME");
        env.remove("PYTHONDONTWRITEBYTECODE");

        env.put("PROOT_FORCE_KOMPAT", "1");
        env.put("GLIBC_TUNABLES", "glibc.pthread.rseq=0");
        env.put("TERM", "xterm-256color");
        env.put("HOME", "/root");

        File prootTmp = new File(prootRuntimeDir(), "tmp");
        env.put("PROOT_TMP_DIR", prootTmp.getAbsolutePath());
        env.put("TMPDIR", prootTmp.getAbsolutePath());

        // Resolve proot-loader from APK native libs
        String loaderPath = resolveNativeLibPath("libproot-loader.so");
        if (loaderPath != null) {
            env.put("PROOT_LOADER", loaderPath);
        }

        // LD_LIBRARY_PATH for proot's own dependencies
        String nativeLibDir = resolveNativeLibDir();
        if (nativeLibDir != null) {
            String existing = env.getOrDefault("LD_LIBRARY_PATH", "");
            env.put("LD_LIBRARY_PATH",
                existing.isEmpty() ? nativeLibDir : nativeLibDir + ":" + existing);
        }
    }

    // ── Path resolution ─────────────────────────────────────────────────

    private File llmRuntimeDir() {
        return new File(context.getFilesDir(), LLM_RUNTIME_SUBDIR);
    }

    private File llamaCppDir() {
        return new File(llmRuntimeDir(), LLAMA_DIR);
    }

    private File modelsDir() {
        return new File(llmRuntimeDir(), MODELS_DIR);
    }

    private File downloadsDir() {
        return new File(llmRuntimeDir(), DOWNLOADS_DIR);
    }

    private File modelFile() {
        return new File(modelsDir(), MODEL_NAME);
    }

    private File activeModelMarkerFile() {
        return new File(modelsDir(), ACTIVE_MODEL_MARKER);
    }

    public synchronized List<String> listInstalledModels() {
        File dir = modelsDir();
        if (!dir.isDirectory()) return Collections.emptyList();
        File[] entries = dir.listFiles((file) -> file != null && file.isFile() && file.getName().toLowerCase().endsWith(".gguf"));
        if (entries == null || entries.length == 0) return Collections.emptyList();
        Arrays.sort(entries, Comparator.comparing(File::getName, String.CASE_INSENSITIVE_ORDER));
        List<String> names = new ArrayList<>(entries.length);
        for (File entry : entries) {
            names.add(entry.getName());
        }
        return names;
    }

    public synchronized String getActiveModelName() {
        File active = getActiveModelFile();
        return active == null ? "" : active.getName();
    }

    public synchronized void setActiveModel(String modelName) throws IOException {
        String normalized = normalizeModelName(modelName, null);
        File model = new File(modelsDir(), normalized);
        if (!model.isFile() || model.length() <= 0) {
            throw new IOException("Modell nicht gefunden: " + normalized);
        }
        DownloadHelper.ensureDirectory(modelsDir());
        try (FileOutputStream output = new FileOutputStream(activeModelMarkerFile(), false)) {
            output.write(normalized.getBytes(StandardCharsets.UTF_8));
            output.flush();
        }
    }

    private synchronized File getActiveModelFile() {
        File dir = modelsDir();
        if (!dir.isDirectory()) return null;

        String marker = readTextQuiet(activeModelMarkerFile()).trim();
        if (!marker.isBlank()) {
            File active = new File(dir, marker);
            if (active.isFile() && active.length() > 0) return active;
        }

        File defaultModel = modelFile();
        if (defaultModel.isFile() && defaultModel.length() > 0) {
            return defaultModel;
        }

        List<String> installed = listInstalledModels();
        if (installed.isEmpty()) return null;
        File first = new File(dir, installed.get(0));
        return first.isFile() ? first : null;
    }

    private void tryAutoInstallBundledServer() {
        if (state != State.IDLE || isServerInstalled()) return;
        try {
            installBundledServerIfAvailable(null);
        } catch (Exception error) {
            Log.w(TAG, "Bundled llama-server setup failed: " + error.getMessage());
        }
    }

    private boolean installBundledServerIfAvailable(DownloadHelper.ProgressListener listener) throws Exception {
        if (!assetExists(BUNDLED_LLAMA_TARBALL_ASSET)) return false;

        File llamaDir = DownloadHelper.ensureDirectory(llamaCppDir());
        File downloadsDir = DownloadHelper.ensureDirectory(downloadsDir());
        File bundledTarball = new File(downloadsDir, "bundled-llama-" + LLAMA_VERSION + ".tar.gz");

        if (listener != null) {
            listener.onProgress("extracting", "Gebuendelten llama.cpp Server aus APK vorbereiten...", -1, -1);
        }
        copyAssetToFile(BUNDLED_LLAMA_TARBALL_ASSET, bundledTarball);
        DownloadHelper.verifySha256(bundledTarball, LLAMA_TARBALL_SHA256);
        extractTarGzToDirectory(bundledTarball, llamaDir);
        bundledTarball.delete();

        for (String bin : new String[]{"llama-server", "llama-cli"}) {
            File f = new File(llamaDir, bin);
            if (f.exists()) f.setExecutable(true, false);
        }
        new FileOutputStream(new File(llamaDir, ".version-" + LLAMA_VERSION)).close();
        if (listener != null) {
            listener.onProgress("done", "Gebuendelter llama.cpp Server installiert.", -1, -1);
        }
        return true;
    }

    private boolean assetExists(String assetPath) {
        try (InputStream ignored = context.getAssets().open(assetPath)) {
            return true;
        } catch (IOException ignored) {
            return false;
        }
    }

    private void copyAssetToFile(String assetPath, File target) throws IOException {
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("Could not create directory: " + parent.getAbsolutePath());
        }
        try (InputStream input = context.getAssets().open(assetPath);
             FileOutputStream output = new FileOutputStream(target, false)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            output.flush();
        }
    }

    private String normalizeModelUrl(String requestedModelUrl) {
        String url = requestedModelUrl == null ? "" : requestedModelUrl.trim();
        return url.isEmpty() ? MODEL_URL : url;
    }

    private String normalizeModelName(String requestedModelName, String modelUrl) {
        String name = requestedModelName == null ? "" : requestedModelName.trim();
        if (name.isEmpty() && modelUrl != null) {
            int slash = modelUrl.lastIndexOf('/');
            if (slash >= 0 && slash + 1 < modelUrl.length()) {
                name = modelUrl.substring(slash + 1);
            }
        }
        if (name.isEmpty()) {
            name = MODEL_NAME;
        }
        int queryIdx = name.indexOf('?');
        if (queryIdx >= 0) name = name.substring(0, queryIdx);
        name = name.replace("\\", "/");
        int sep = name.lastIndexOf('/');
        if (sep >= 0) name = name.substring(sep + 1);
        if (name.isBlank()) {
            throw new IllegalArgumentException("Modell-Dateiname darf nicht leer sein.");
        }
        if (!name.toLowerCase().endsWith(".gguf")) {
            throw new IllegalArgumentException("Nur .gguf Modelle werden unterstuetzt.");
        }
        if (name.contains("..")) {
            throw new IllegalArgumentException("Ungueltiger Modell-Dateiname.");
        }
        return name;
    }

    private String normalizeModelSha256(String requestedSha, String modelName, String modelUrl) {
        String sha = requestedSha == null ? "" : requestedSha.trim();
        if (sha.isEmpty() && MODEL_NAME.equals(modelName) && MODEL_URL.equals(modelUrl)) {
            return MODEL_SHA256;
        }
        return sha.isEmpty() ? null : sha;
    }

    private String readTextQuiet(File file) {
        if (file == null || !file.isFile()) return "";
        try (InputStream input = new FileInputStream(file)) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int read;
            while ((read = input.read(chunk)) != -1) {
                buffer.write(chunk, 0, read);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        } catch (IOException ignored) {
            return "";
        }
    }

    private File prootRuntimeDir() {
        return new File(context.getFilesDir(), PROOT_RUNTIME_SUBDIR);
    }

    /** Resolves the proot binary, preferring the embedded APK native lib. */
    private File resolveProotBinary() {
        String embeddedPath = resolveNativeLibPath("libprootclassic.so");
        if (embeddedPath != null) {
            File embedded = new File(embeddedPath);
            if (embedded.exists()) return embedded;
        }
        // Fallback: proot binary in runtime dir
        File runtimeProot = new File(new File(prootRuntimeDir(), "bin"), "proot");
        return runtimeProot.exists() ? runtimeProot : null;
    }

    /** Resolves the Ubuntu rootfs directory. */
    private File resolveUbuntuRootfs() {
        File rootfsDir = new File(new File(new File(prootRuntimeDir(), "distros"), "ubuntu"), "rootfs");
        if (!rootfsDir.isDirectory()) return null;

        // Check if rootfs itself has /bin or /usr/bin
        if (hasUsableRootfs(rootfsDir)) return rootfsDir;

        // Check subdirectories (e.g., ubuntu-questing-aarch64/)
        File[] children = rootfsDir.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (child.isDirectory() && hasUsableRootfs(child)) return child;
        }
        return null;
    }

    private boolean hasUsableRootfs(File dir) {
        return new File(dir, "bin").isDirectory() || new File(dir, "usr/bin").isDirectory();
    }

    private String resolveNativeLibPath(String libName) {
        ApplicationInfo appInfo = context.getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        File lib = new File(appInfo.nativeLibraryDir, libName);
        return lib.isFile() ? lib.getAbsolutePath() : null;
    }

    private String resolveNativeLibDir() {
        ApplicationInfo appInfo = context.getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        return appInfo.nativeLibraryDir;
    }

    // ── Tar extraction ──────────────────────────────────────────────────

    private void extractTarGzToDirectory(File archive, File targetDir) throws IOException {
        // Try system tar first (more reliable for complex archives)
        String systemTarResult = extractWithSystemTar(archive, targetDir);
        if (systemTarResult == null) return;

        // Fallback: manual tar parsing
        try (InputStream fis = new FileInputStream(archive);
             InputStream gis = new GZIPInputStream(fis);
             BufferedInputStream input = new BufferedInputStream(gis)) {
            extractTarStream(input, targetDir);
        }
    }

    private String extractWithSystemTar(File archive, File targetDir) {
        Process process = null;
        try {
            process = new ProcessBuilder(
                "/system/bin/tar", "xzf", archive.getAbsolutePath(),
                "-C", targetDir.getAbsolutePath(), "--strip-components=1"
            ).redirectErrorStream(true).start();

            boolean finished = process.waitFor(120, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                return "timeout";
            }
            if (process.exitValue() == 0) return null;

            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line).append('\n');
                return sb.toString().isBlank() ? "exit " + process.exitValue() : sb.toString();
            }
        } catch (Exception e) {
            return e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
        } finally {
            if (process != null) process.destroy();
        }
    }

    private void extractTarStream(BufferedInputStream input, File targetDir) throws IOException {
        byte[] header = new byte[512];
        while (readFully(input, header)) {
            if (isZeroBlock(header)) break;
            String entryName = tarEntryName(header);
            long size = parseTarOctal(header, 124, 12);
            int mode = (int) parseTarOctal(header, 100, 8);
            char type = (char) (header[156] & 0xff);

            // Skip metadata entries
            if (type == 'x' || type == 'g' || type == 'L' || type == 'K') {
                skipFully(input, size);
                skipFully(input, (512 - (size % 512)) % 512);
                continue;
            }

            // Strip first path component (like --strip-components=1)
            String stripped = stripFirstComponent(entryName);
            if (stripped.isEmpty()) {
                skipFully(input, size);
                skipFully(input, (512 - (size % 512)) % 512);
                continue;
            }

            if (type == '5' || entryName.endsWith("/")) {
                File dir = securePath(targetDir, stripped);
                if (dir != null && !dir.exists()) dir.mkdirs();
            } else if (type == '2') {
                // Symlink
                String linkTarget = readTarString(header, 157, 100);
                File link = securePath(targetDir, stripped);
                if (link != null && linkTarget != null && !linkTarget.isEmpty()) {
                    try {
                        Files.deleteIfExists(link.toPath());
                        Files.createSymbolicLink(link.toPath(), Paths.get(linkTarget));
                    } catch (UnsupportedOperationException ignored) {}
                }
            } else if (type == 0 || type == '0') {
                File outFile = securePath(targetDir, stripped);
                if (outFile != null) {
                    File parent = outFile.getParentFile();
                    if (parent != null && !parent.exists()) parent.mkdirs();
                    try (FileOutputStream out = new FileOutputStream(outFile)) {
                        long remaining = size;
                        byte[] buf = new byte[8192];
                        while (remaining > 0) {
                            int read = input.read(buf, 0, (int) Math.min(buf.length, remaining));
                            if (read == -1) throw new IOException("Unexpected EOF in tar.");
                            out.write(buf, 0, read);
                            remaining -= read;
                        }
                        out.flush();
                    }
                    if ((mode & 0111) != 0) outFile.setExecutable(true, false);
                } else {
                    skipFully(input, size);
                }
            } else {
                skipFully(input, size);
            }
            skipFully(input, (512 - (size % 512)) % 512);
        }
    }

    private String stripFirstComponent(String path) {
        String normalized = path.replace('\\', '/');
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        int slash = normalized.indexOf('/');
        if (slash < 0) return "";
        return normalized.substring(slash + 1);
    }

    private File securePath(File targetDir, String entryName) {
        try {
            File out = new File(targetDir, entryName);
            if (out.getCanonicalPath().startsWith(targetDir.getCanonicalPath())) return out;
        } catch (IOException ignored) {}
        return null;
    }

    private String tarEntryName(byte[] header) {
        String name = readTarString(header, 0, 100);
        String prefix = readTarString(header, 345, 155);
        if (prefix.isEmpty()) return name;
        if (name.isEmpty()) return prefix;
        return prefix + "/" + name;
    }

    private String readTarString(byte[] buffer, int offset, int len) {
        int end = offset;
        while (end < offset + len && buffer[end] != 0) end++;
        return new String(buffer, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    private long parseTarOctal(byte[] buffer, int offset, int len) {
        if ((buffer[offset] & 0x80) != 0) {
            long value = buffer[offset] & 0x7fL;
            for (int i = 1; i < len; i++) value = (value << 8) | (buffer[offset + i] & 0xffL);
            return value;
        }
        String raw = readTarString(buffer, offset, len);
        if (raw.isEmpty()) return 0L;
        try { return Long.parseLong(raw.trim(), 8); }
        catch (NumberFormatException e) { return 0L; }
    }

    private boolean readFully(InputStream input, byte[] buffer) throws IOException {
        int total = 0;
        while (total < buffer.length) {
            int read = input.read(buffer, total, buffer.length - total);
            if (read == -1) return total > 0;
            total += read;
        }
        return true;
    }

    private boolean isZeroBlock(byte[] block) {
        for (byte b : block) if (b != 0) return false;
        return true;
    }

    private void skipFully(InputStream input, long bytes) throws IOException {
        long remaining = bytes;
        while (remaining > 0) {
            long skipped = input.skip(remaining);
            if (skipped <= 0) {
                if (input.read() == -1) throw new IOException("Unexpected EOF while skipping.");
                skipped = 1;
            }
            remaining -= skipped;
        }
    }

    // ── Status DTO ──────────────────────────────────────────────────────

    public static final class LlmSetupStatus {
        public final boolean prootReady;
        public final boolean serverInstalled;
        public final boolean modelInstalled;
        public final boolean serverRunning;
        public final String state;
        public final String lastError;
        public final String llamaVersion;
        public final String modelName;
        public final int serverPort;

        LlmSetupStatus(boolean prootReady, boolean serverInstalled, boolean modelInstalled,
                        boolean serverRunning, String state, String lastError,
                        String llamaVersion, String modelName, int serverPort) {
            this.prootReady = prootReady;
            this.serverInstalled = serverInstalled;
            this.modelInstalled = modelInstalled;
            this.serverRunning = serverRunning;
            this.state = state;
            this.lastError = lastError;
            this.llamaVersion = llamaVersion;
            this.modelName = modelName;
            this.serverPort = serverPort;
        }
    }
}
