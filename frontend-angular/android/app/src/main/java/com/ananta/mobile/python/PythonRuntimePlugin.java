package com.ananta.mobile.python;

import android.content.Context;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.zip.GZIPInputStream;

import org.tukaani.xz.XZInputStream;

@CapacitorPlugin(name = "PythonRuntime")
public class PythonRuntimePlugin extends Plugin {
    private static final int DEFAULT_SHELL_TIMEOUT_SECONDS = 20;
    private static final int MAX_SHELL_TIMEOUT_SECONDS = 600;
    private static final int MAX_SHELL_OUTPUT_CHARS = 120_000;
    private static final int MAX_SESSION_OUTPUT_CHARS = 200_000;
    private static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    private static final String PROOT_BIN_FILE = "proot-rs";
    private static final String PROOT_CLASSIC_FILE = "proot-classic";
    private static final String PROOT_WRAPPER_FILE = "proot";
    private static final String PROOT_EMBEDDED_LIB_FILE = "libprootrs.so";
    private static final String PROOT_RS_RELEASE_URL = "https://github.com/proot-me/proot-rs/releases/download/v0.1.0/proot-rs-v0.1.0-aarch64-linux-android.tar.gz";
    private static final String PROOT_CLASSIC_RELEASE_URL = "https://github.com/proot-me/proot/releases/download/v5.3.0/proot-v5.3.0-aarch64-static";
    private static final String PROOT_DISTRO_RELEASE_API = "https://api.github.com/repos/termux/proot-distro/releases/latest";
    private static final String PROOT_DISTRO_PLUGIN_BASE = "https://raw.githubusercontent.com/termux/proot-distro/master/distro-plugins/";

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Map<String, ShellSession> shellSessions = new ConcurrentHashMap<>();
    private volatile boolean hubRunning;
    private volatile boolean workerRunning;
    private volatile String lastError;

    @PluginMethod
    public void getRuntimeStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("pythonAvailable", isPythonAvailable());
        result.put("hubRunning", hubRunning);
        result.put("workerRunning", workerRunning);
        result.put("lastError", lastError);
        call.resolve(result);
    }

    @PluginMethod
    public void startHub(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("start_hub");
                hubRunning = true;
                JSObject result = new JSObject();
                result.put("hubRunning", true);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Hub start failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void stopHub(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("stop_hub");
                hubRunning = false;
                JSObject result = new JSObject();
                result.put("hubRunning", false);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Hub stop failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void startWorker(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("start_worker");
                workerRunning = true;
                JSObject result = new JSObject();
                result.put("workerRunning", true);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Worker start failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void stopWorker(PluginCall call) {
        worker.execute(() -> {
            try {
                invokePython("stop_worker");
                workerRunning = false;
                JSObject result = new JSObject();
                result.put("workerRunning", false);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Worker stop failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void runHealthCheck(PluginCall call) {
        worker.execute(() -> {
            try {
                String value = invokePython("health_check");
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("message", value);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Health check failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void runShellCommand(PluginCall call) {
        String command = String.valueOf(call.getString("command", "")).trim();
        if (command.isEmpty()) {
            call.reject("command is required");
            return;
        }

        int timeoutSeconds = call.getInt("timeoutSeconds", DEFAULT_SHELL_TIMEOUT_SECONDS);
        if (timeoutSeconds < 1 || timeoutSeconds > MAX_SHELL_TIMEOUT_SECONDS) {
            call.reject("timeoutSeconds must be between 1 and " + MAX_SHELL_TIMEOUT_SECONDS);
            return;
        }

        worker.execute(() -> {
            try {
                ShellExecutionResult exec = executeShellCommand(command, timeoutSeconds);
                JSObject result = new JSObject();
                result.put("output", exec.output);
                result.put("exitCode", exec.exitCode);
                result.put("timedOut", exec.timedOut);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell execution failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void openShellSession(PluginCall call) {
        String cwd = String.valueOf(call.getString("cwd", "")).trim();
        String initialCommand = String.valueOf(call.getString("initialCommand", "")).trim();
        String shell = String.valueOf(call.getString("shell", "sh")).trim();
        if (shell.isEmpty()) shell = "sh";

        final String selectedShell = shell;
        final String selectedCwd = cwd;
        final String selectedInitialCommand = initialCommand;
        worker.execute(() -> {
            try {
                File workingDir = resolveShellWorkingDirectory(selectedCwd);
                ProcessBuilder builder = new ProcessBuilder(selectedShell);
                builder.directory(workingDir);
                applyShellEnvironment(builder, workingDir);
                Process process = builder.redirectErrorStream(true).start();
                ShellSession session = new ShellSession(process);
                String sessionId = UUID.randomUUID().toString();
                shellSessions.put(sessionId, session);
                session.startReaderThread();
                if (!selectedInitialCommand.isEmpty()) {
                    session.write(selectedInitialCommand + "\n");
                }

                JSObject result = new JSObject();
                result.put("sessionId", sessionId);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell session start failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void writeShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        String input = String.valueOf(call.getString("input", ""));
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        ShellSession session = shellSessions.get(sessionId);
        if (session == null) {
            call.reject("Shell session not found");
            return;
        }
        worker.execute(() -> {
            try {
                session.write(input);
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell write failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void readShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        int maxChars = call.getInt("maxChars", 8000);
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        if (maxChars < 256) maxChars = 256;
        if (maxChars > 32000) maxChars = 32000;
        ShellSession session = shellSessions.get(sessionId);
        if (session == null) {
            call.reject("Shell session not found");
            return;
        }

        final int maxCharsFinal = maxChars;
        worker.execute(() -> {
            try {
                ShellSessionRead read = session.readDelta(maxCharsFinal);
                JSObject result = new JSObject();
                result.put("output", read.output);
                result.put("hasMore", read.hasMore);
                result.put("running", session.isRunning());
                if (!session.isRunning()) {
                    result.put("exitCode", session.exitCode());
                }
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell read failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void closeShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
        if (sessionId.isEmpty()) {
            call.reject("sessionId is required");
            return;
        }
        ShellSession session = shellSessions.remove(sessionId);
        if (session == null) {
            JSObject result = new JSObject();
            result.put("closed", true);
            call.resolve(result);
            return;
        }

        worker.execute(() -> {
            session.close();
            JSObject result = new JSObject();
            result.put("closed", true);
            call.resolve(result);
        });
    }

    @PluginMethod
    public void getProotRuntimeStatus(PluginCall call) {
        worker.execute(() -> {
            try {
                JSObject result = new JSObject();
                File runtimeRoot = runtimeRootDir();
                File prootWrapper = new File(runtimeRoot, "bin/" + PROOT_WRAPPER_FILE);
                File classicProotBinary = new File(runtimeRoot, "bin/" + PROOT_CLASSIC_FILE);
                File runtimeProotBinary = new File(runtimeRoot, "bin/" + PROOT_BIN_FILE);
                File embeddedProotBinary = embeddedNativeProotBinary();
                File selectedProotBinary = resolveProotBinaryCandidate(classicProotBinary, runtimeProotBinary, embeddedProotBinary);
                File distroRoot = new File(runtimeRoot, "distros");
                JSArray distros = new JSArray();
                File[] entries = distroRoot.listFiles();
                if (entries != null) {
                    for (File entry : entries) {
                        if (!entry.isDirectory()) continue;
                        File rootfs = new File(entry, "rootfs");
                        if (!rootfs.isDirectory()) continue;
                        JSObject item = new JSObject();
                        item.put("name", entry.getName());
                        item.put("rootfsPath", rootfs.getAbsolutePath());
                        distros.put(item);
                    }
                }

                result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                result.put("prootPath", prootWrapper.getAbsolutePath());
                result.put("prootBinaryPath", selectedProotBinary != null ? selectedProotBinary.getAbsolutePath() : "");
                result.put("prootBinarySource", isEmbeddedBinary(selectedProotBinary, embeddedProotBinary) ? "embedded-native-lib" : "runtime-files");
                boolean prootExists = prootWrapper.exists() && selectedProotBinary != null && selectedProotBinary.exists();
                result.put("prootExists", prootExists);
                ProotProbeResult probe = new ProotProbeResult(false, "Runtime nicht installiert.");
                if (prootExists) {
                    prootWrapper.setReadable(true, false);
                    selectedProotBinary.setReadable(true, false);
                    prootWrapper.setExecutable(true, false);
                    selectedProotBinary.setExecutable(true, false);
                    createProotWrapper(new File(runtimeRoot, "bin"), selectedProotBinary);
                    probe = probeProotWrapper(prootWrapper);
                }
                result.put("prootExecutable", prootExists && probe.runnable);
                result.put("prootProbeMessage", probe.message);
                result.put("distros", distros);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Could not read proot runtime status: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installProotRuntime(PluginCall call) {
        worker.execute(() -> {
            try {
                String url = String.valueOf(call.getString("prootUrl", PROOT_RS_RELEASE_URL)).trim();
                if (url.isEmpty()) url = PROOT_RS_RELEASE_URL;
                String classicUrl = String.valueOf(call.getString("prootClassicUrl", PROOT_CLASSIC_RELEASE_URL)).trim();
                if (classicUrl.isEmpty()) classicUrl = PROOT_CLASSIC_RELEASE_URL;
                notifyProotProgress("runtime", "preparing", "Runtime-Installation gestartet.", -1, -1, null);

                File runtimeRoot = runtimeRootDir();
                File binDir = ensureDir(runtimeRoot, "bin");
                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File classicBinary = new File(binDir, PROOT_CLASSIC_FILE);
                if (!isUsableBinary(classicBinary)) {
                    File classicDownload = new File(tmpDir, "proot-classic-aarch64-static");
                    notifyProotProgress("runtime", "downloading", "Klassische proot Runtime wird geladen.", 0, -1, null);
                    downloadToFile(classicUrl, classicDownload, "runtime");
                    copyFile(classicDownload, classicBinary);
                    if (!classicBinary.setExecutable(true, false)) {
                        throw new IOException("Could not mark classic proot binary executable.");
                    }
                }
                createProotWrapper(binDir, classicBinary);
                ProotProbeResult classicProbe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                if (classicProbe.runnable) {
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", classicBinary.getAbsolutePath());
                    result.put("prootBinarySource", "classic-static");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", true);
                    result.put("probeMessage", classicProbe.message);
                    notifyProotProgress("runtime", "done", "Runtime bereit (klassisches proot).", -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File embeddedProotBinary = embeddedNativeProotBinary();
                if (isUsableBinary(embeddedProotBinary)) {
                    createProotWrapper(binDir, embeddedProotBinary);
                    ProotProbeResult probe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", embeddedProotBinary.getAbsolutePath());
                    result.put("prootBinarySource", "embedded-native-lib");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereit (APK-native binary)."
                        : "Runtime vorhanden (APK-native binary), aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File existingWrapper = new File(binDir, PROOT_WRAPPER_FILE);
                File existingBinary = new File(binDir, PROOT_BIN_FILE);
                if (existingWrapper.exists() && existingBinary.exists()) {
                    existingWrapper.setReadable(true, false);
                    existingBinary.setReadable(true, false);
                    existingWrapper.setExecutable(true, false);
                    existingBinary.setExecutable(true, false);
                    ProotProbeResult probe = probeProotWrapper(existingWrapper);
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", existingWrapper.getAbsolutePath());
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereits installiert."
                        : "Runtime bereits installiert, aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

                File downloadTarget = new File(tmpDir, "proot-rs.tar.gz");
                downloadToFile(url, downloadTarget, "runtime");
                notifyProotProgress("runtime", "extracting", "Runtime wird entpackt.", -1, -1, null);

                File extractedBinary = extractFirstExecutableFromTarGz(downloadTarget, tmpDir, "proot-rs");
                if (extractedBinary == null || !extractedBinary.exists()) {
                    throw new IOException("No executable proot-rs binary found in archive.");
                }

                File targetBinary = new File(binDir, PROOT_BIN_FILE);
                copyFile(extractedBinary, targetBinary);
                if (!targetBinary.setExecutable(true, false)) {
                    throw new IOException("Could not mark proot binary executable.");
                }
                createProotWrapper(binDir, targetBinary);

                JSObject result = new JSObject();
                result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                notifyProotProgress("runtime", "done", "Runtime installiert.", -1, -1, null);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("runtime", "error", error.getMessage(), -1, -1, null);
                call.reject("Proot runtime installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installProotDistro(PluginCall call) {
        String distro = String.valueOf(call.getString("distro", "")).trim().toLowerCase();
        if (distro.isEmpty()) {
            call.reject("distro is required");
            return;
        }

        worker.execute(() -> {
            try {
                notifyProotProgress("distro", "preparing", "Distro-Installation gestartet.", -1, -1, distro);
                ensureProotInstalled();
                DistroDownloadMeta downloadMeta = resolveDistroDownloadMeta(distro);
                String assetUrl = downloadMeta.url;
                if (assetUrl == null || assetUrl.isBlank()) {
                    throw new IOException("No aarch64 archive found for distro: " + distro);
                }

                File runtimeRoot = runtimeRootDir();
                File distrosDir = ensureDir(runtimeRoot, "distros");
                File distroDir = ensureDir(distrosDir, distro);
                File rootfsDir = ensureDir(distroDir, "rootfs");
                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File archive = new File(tmpDir, distro + "-rootfs.tar.xz");
                downloadToFile(assetUrl, archive, "distro", distro);
                notifyProotProgress("distro", "extracting", "Distro wird entpackt.", -1, -1, distro);
                if (downloadMeta.sha256 != null && !downloadMeta.sha256.isBlank()) {
                    String actualSha = computeSha256(archive);
                    if (!actualSha.equalsIgnoreCase(downloadMeta.sha256)) {
                        throw new IOException("SHA256 mismatch for distro archive.");
                    }
                }
                clearDirectory(rootfsDir);
                extractTarXzToDirectory(archive, rootfsDir);

                JSObject result = new JSObject();
                result.put("distro", distro);
                result.put("rootfsPath", rootfsDir.getAbsolutePath());
                notifyProotProgress("distro", "done", "Distro installiert.", -1, -1, distro);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("distro", "error", error.getMessage(), -1, -1, distro);
                call.reject("Distro installation failed: " + error.getMessage());
            }
        });
    }

    @Override
    protected void handleOnDestroy() {
        for (ShellSession session : shellSessions.values()) {
            session.close();
        }
        shellSessions.clear();
        worker.shutdownNow();
        super.handleOnDestroy();
    }

    private boolean isPythonAvailable() {
        try {
            Class.forName("com.chaquo.python.Python");
            return true;
        } catch (ClassNotFoundException error) {
            return false;
        }
    }

    private String invokePython(String functionName) throws Exception {
        if (!isPythonAvailable()) {
            throw new IllegalStateException("Embedded Python is disabled. Enable anantaEnablePythonRuntime=true.");
        }

        ensurePythonStarted();
        Class<?> pythonClass = Class.forName("com.chaquo.python.Python");
        Method getInstance = pythonClass.getMethod("getInstance");
        Object python = getInstance.invoke(null);
        Method getModule = pythonClass.getMethod("getModule", String.class);
        Object module = getModule.invoke(python, "ananta_runtime");

        Class<?> pyObjectClass = Class.forName("com.chaquo.python.PyObject");
        Method callAttr = pyObjectClass.getMethod("callAttr", String.class, Object[].class);
        Object value = callAttr.invoke(module, functionName, new Object[]{});
        return value == null ? "" : String.valueOf(value);
    }

    private void ensurePythonStarted() throws Exception {
        Class<?> pythonClass = Class.forName("com.chaquo.python.Python");
        Method isStarted = pythonClass.getMethod("isStarted");
        boolean started = (boolean) isStarted.invoke(null);
        if (started) return;

        Context context = getContext();
        if (context == null) {
            throw new IllegalStateException("Android context unavailable for Python startup.");
        }

        Class<?> androidPlatformClass = Class.forName("com.chaquo.python.android.AndroidPlatform");
        Object androidPlatform = androidPlatformClass.getConstructor(Context.class).newInstance(context);

        Method startMethod = null;
        for (Method candidate : pythonClass.getMethods()) {
            if (!"start".equals(candidate.getName()) || candidate.getParameterCount() != 1) continue;
            Class<?> parameterType = candidate.getParameterTypes()[0];
            if (parameterType.isAssignableFrom(androidPlatformClass)) {
                startMethod = candidate;
                break;
            }
        }
        if (startMethod == null) {
            throw new NoSuchMethodException("Python.start(...) compatible with AndroidPlatform not found.");
        }
        startMethod.invoke(null, androidPlatform);
    }

    private ShellExecutionResult executeShellCommand(String command, int timeoutSeconds) throws Exception {
        File workingDir = resolveShellWorkingDirectory("");
        ProcessBuilder builder = new ProcessBuilder("sh", "-lc", command);
        builder.directory(workingDir);
        applyShellEnvironment(builder, workingDir);
        Process process = builder
            .redirectErrorStream(true)
            .start();

        boolean finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS);
        if (!finished) {
            process.destroy();
            if (!process.waitFor(1, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                process.waitFor(1, TimeUnit.SECONDS);
            }
        }

        String output = readProcessOutput(process.getInputStream(), MAX_SHELL_OUTPUT_CHARS);
        if (!finished) {
            output = (output + "\n[ananta-mobile-shell] Timeout after " + timeoutSeconds + "s").trim();
            return new ShellExecutionResult(output, -1, true);
        }

        return new ShellExecutionResult(output, process.exitValue(), false);
    }

    private File resolveShellWorkingDirectory(String requestedCwd) {
        String cwd = String.valueOf(requestedCwd == null ? "" : requestedCwd).trim();
        if (!cwd.isEmpty()) {
            File requested = new File(cwd);
            if (requested.isDirectory() && requested.canRead()) {
                return requested;
            }
        }

        Context context = getContext();
        if (context != null) {
            File filesDir = context.getFilesDir();
            if (filesDir != null && filesDir.isDirectory() && filesDir.canRead()) {
                return filesDir;
            }
        }

        File termuxHome = new File("/data/data/com.termux/files/home");
        if (termuxHome.isDirectory() && termuxHome.canRead()) {
            return termuxHome;
        }

        return new File("/");
    }

    private void applyShellEnvironment(ProcessBuilder builder, File workingDir) {
        Map<String, String> env = builder.environment();
        String path = workingDir.getAbsolutePath();
        env.put("HOME", path);
        env.put("PWD", path);
        env.put("ANANTA_MOBILE_FILES", path);
        File runtimeRoot = runtimeRootDir();
        File prootBin = new File(runtimeRoot, "bin");
        String existingPath = String.valueOf(env.getOrDefault("PATH", ""));
        env.put("PATH", prootBin.getAbsolutePath() + (existingPath.isEmpty() ? "" : ":" + existingPath));
        env.putIfAbsent("TERM", "xterm-256color");
    }

    private File runtimeRootDir() {
        return new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR);
    }

    private File ensureDir(File parent, String child) throws IOException {
        File dir = new File(parent, child);
        if (dir.exists()) {
            if (dir.isDirectory()) return dir;
            throw new IOException("Path is not a directory: " + dir.getAbsolutePath());
        }
        if (dir.mkdirs()) return dir;
        throw new IOException("Could not create directory: " + dir.getAbsolutePath());
    }

    private void ensureProotInstalled() throws IOException {
        File binDir = new File(runtimeRootDir(), "bin");
        File wrapper = new File(binDir, PROOT_WRAPPER_FILE);
        File runtimeBinary = new File(binDir, PROOT_BIN_FILE);
        File classicBinary = new File(binDir, PROOT_CLASSIC_FILE);
        File selectedBinary = resolveProotBinaryCandidate(classicBinary, runtimeBinary, embeddedNativeProotBinary());
        if (!wrapper.exists() || !wrapper.canExecute() || selectedBinary == null || !selectedBinary.exists()) {
            throw new IOException("Proot runtime is not installed. Install runtime first.");
        }
        selectedBinary.setReadable(true, false);
        selectedBinary.setExecutable(true, false);
        createProotWrapper(binDir, selectedBinary);
    }

    private void createProotWrapper(File binDir, File targetBinary) throws IOException {
        File wrapper = new File(binDir, PROOT_WRAPPER_FILE);
        String script = "#!/system/bin/sh\nexec \"" + targetBinary.getAbsolutePath() + "\" \"$@\"\n";
        try (FileOutputStream output = new FileOutputStream(wrapper, false)) {
            output.write(script.getBytes(StandardCharsets.UTF_8));
            output.flush();
        }
        if (!wrapper.setExecutable(true, false)) {
            throw new IOException("Could not mark proot wrapper executable.");
        }
    }

    private File embeddedNativeProotBinary() {
        Context context = getContext();
        if (context == null || context.getApplicationInfo() == null) return null;
        String nativeDir = String.valueOf(context.getApplicationInfo().nativeLibraryDir == null ? "" : context.getApplicationInfo().nativeLibraryDir).trim();
        if (nativeDir.isEmpty()) return null;
        File candidate = new File(nativeDir, PROOT_EMBEDDED_LIB_FILE);
        return candidate.exists() ? candidate : null;
    }

    private File resolveProotBinaryCandidate(File classicBinary, File runtimeBinary, File embeddedBinary) {
        if (isUsableBinary(classicBinary)) return classicBinary;
        if (isUsableBinary(embeddedBinary)) return embeddedBinary;
        if (isUsableBinary(runtimeBinary)) return runtimeBinary;
        return null;
    }

    private boolean isUsableBinary(File file) {
        return file != null && file.exists() && file.isFile();
    }

    private boolean isEmbeddedBinary(File selected, File embedded) {
        if (selected == null || embedded == null) return false;
        try {
            return selected.getCanonicalPath().equals(embedded.getCanonicalPath());
        } catch (IOException ignored) {
            return selected.getAbsolutePath().equals(embedded.getAbsolutePath());
        }
    }

    private ProotProbeResult probeProotWrapper(File wrapper) {
        Process process = null;
        try {
            process = new ProcessBuilder("/system/bin/sh", wrapper.getAbsolutePath(), "--version")
                .redirectErrorStream(true)
                .start();
            boolean finished = process.waitFor(8, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                return new ProotProbeResult(false, "Runtime-Check Timeout.");
            }
            String output = readProcessOutput(process.getInputStream(), 4000);
            int exitCode = process.exitValue();
            if (exitCode == 0) {
                return new ProotProbeResult(true, output.isBlank() ? "Runtime startbar." : output);
            }
            String details = output.isBlank() ? "Exit " + exitCode : output;
            return new ProotProbeResult(false, "Runtime nicht startbar: " + details);
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage() == null ? "" : error.getMessage()).trim();
            if (message.isEmpty()) message = error.getClass().getSimpleName();
            return new ProotProbeResult(false, "Runtime-Check fehlgeschlagen: " + message);
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }

    private void downloadToFile(String rawUrl, File targetFile, String operation) throws IOException {
        downloadToFile(rawUrl, targetFile, operation, null);
    }

    private void downloadToFile(String rawUrl, File targetFile, String operation, String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(rawUrl);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(180000);
            connection.setInstanceFollowRedirects(true);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Download failed with HTTP " + status + " from " + rawUrl);
            }
            long totalBytes = connection.getContentLengthLong();
            notifyProotProgress(operation, "downloading", "Download gestartet.", 0, totalBytes, distro);
            try (InputStream input = new BufferedInputStream(connection.getInputStream());
                 FileOutputStream output = new FileOutputStream(targetFile, false)) {
                byte[] buffer = new byte[8192];
                int read;
                long downloaded = 0L;
                long nextReport = 256 * 1024;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    downloaded += read;
                    if (downloaded >= nextReport) {
                        notifyProotProgress(operation, "downloading", "Download laeuft...", downloaded, totalBytes, distro);
                        nextReport = downloaded + (256 * 1024);
                    }
                }
                output.flush();
                notifyProotProgress(operation, "downloading", "Download abgeschlossen.", downloaded, totalBytes, distro);
            }
            if (!targetFile.exists() || targetFile.length() == 0) {
                throw new IOException("Downloaded file is empty: " + targetFile.getAbsolutePath());
            }
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void notifyProotProgress(String operation, String stage, String message, long downloaded, long total, String distro) {
        JSObject event = new JSObject();
        event.put("operation", operation);
        event.put("stage", stage);
        event.put("message", message);
        event.put("downloadedBytes", downloaded);
        event.put("totalBytes", total);
        if (total > 0 && downloaded >= 0) {
            event.put("progress", Math.min(1.0, (double) downloaded / (double) total));
        } else {
            event.put("progress", -1);
        }
        if (distro != null && !distro.isBlank()) {
            event.put("distro", distro);
        }
        notifyListeners("prootInstallProgress", event);
    }

    private DistroDownloadMeta resolveDistroDownloadMeta(String distro) throws IOException {
        DistroDownloadMeta fromPlugin = resolveDistroFromPluginScript(distro);
        if (fromPlugin != null && fromPlugin.url != null && !fromPlugin.url.isBlank()) {
            return fromPlugin;
        }
        String fromRelease = resolveDistroAssetUrlFromRelease(distro);
        if (fromRelease == null || fromRelease.isBlank()) {
            return new DistroDownloadMeta(null, null);
        }
        return new DistroDownloadMeta(fromRelease, null);
    }

    private DistroDownloadMeta resolveDistroFromPluginScript(String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(PROOT_DISTRO_PLUGIN_BASE + distro + ".sh");
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                return null;
            }
            String body = readProcessOutput(connection.getInputStream(), 200_000);
            String urlMarker = "TARBALL_URL['aarch64']=\"";
            int urlStart = body.indexOf(urlMarker);
            if (urlStart < 0) return null;
            int urlValueStart = urlStart + urlMarker.length();
            int urlValueEnd = body.indexOf('"', urlValueStart);
            if (urlValueEnd <= urlValueStart) return null;
            String archiveUrl = body.substring(urlValueStart, urlValueEnd).trim();

            String shaMarker = "TARBALL_SHA256['aarch64']=\"";
            int shaStart = body.indexOf(shaMarker);
            String sha = null;
            if (shaStart >= 0) {
                int shaValueStart = shaStart + shaMarker.length();
                int shaValueEnd = body.indexOf('"', shaValueStart);
                if (shaValueEnd > shaValueStart) {
                    sha = body.substring(shaValueStart, shaValueEnd).trim();
                }
            }
            return new DistroDownloadMeta(archiveUrl, sha);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private String resolveDistroAssetUrlFromRelease(String distro) throws IOException {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(PROOT_DISTRO_RELEASE_API);
            connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "ananta-mobile");
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("Could not query distro release API. HTTP " + status);
            }
            String body = readProcessOutput(connection.getInputStream(), 200_000);
            String marker = "\"" + distro + "-aarch64-pd-";
            int markerIndex = body.indexOf(marker);
            if (markerIndex < 0) return null;
            int urlKeyIndex = body.indexOf("\"browser_download_url\":\"", markerIndex);
            if (urlKeyIndex < 0) return null;
            int start = urlKeyIndex + "\"browser_download_url\":\"".length();
            int end = body.indexOf('"', start);
            if (end <= start) return null;
            String escaped = body.substring(start, end);
            return escaped.replace("\\/", "/");
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private File extractFirstExecutableFromTarGz(File archive, File tempDir, String preferredName) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream gis = new GZIPInputStream(fis);
             BufferedInputStream input = new BufferedInputStream(gis)) {
            byte[] header = new byte[512];
            while (readFully(input, header, 0, header.length)) {
                if (isZeroBlock(header)) break;
                String entryName = tarEntryName(header);
                long size = parseTarOctal(header, 124, 12);
                char type = (char) (header[156] & 0xff);
                boolean regular = type == 0 || type == '0';
                String baseName = baseName(entryName);
                boolean candidate = regular && size > 0 && (baseName.equals(preferredName) || baseName.startsWith(preferredName));

                File extracted = null;
                FileOutputStream output = null;
                if (candidate) {
                    extracted = new File(tempDir, baseName);
                    output = new FileOutputStream(extracted, false);
                }

                long remaining = size;
                byte[] buffer = new byte[8192];
                while (remaining > 0) {
                    int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
                    if (read == -1) throw new IOException("Unexpected EOF while reading tar entry.");
                    if (output != null) output.write(buffer, 0, read);
                    remaining -= read;
                }
                if (output != null) {
                    output.flush();
                    output.close();
                    extracted.setExecutable(true, false);
                    return extracted;
                }
                skipFully(input, (512 - (size % 512)) % 512);
            }
        }
        return null;
    }

    private void extractTarXzToDirectory(File archive, File targetDir) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream xis = new XZInputStream(fis);
             BufferedInputStream input = new BufferedInputStream(xis)) {
            extractTarStreamToDirectory(input, targetDir);
        }
    }

    private void extractTarStreamToDirectory(InputStream input, File targetDir) throws IOException {
        byte[] header = new byte[512];
        while (readFully(input, header, 0, header.length)) {
            if (isZeroBlock(header)) break;
            String entryName = tarEntryName(header);
            long size = parseTarOctal(header, 124, 12);
            int mode = (int) parseTarOctal(header, 100, 8);
            char type = (char) (header[156] & 0xff);
            String linkName = readTarString(header, 157, 100);

            // Skip root marker entries and metadata-only tar records (pax/gnu extensions).
            if (isRootMarkerEntry(entryName) || isMetadataOnlyEntry(type)) {
                skipFully(input, size);
                skipFully(input, (512 - (size % 512)) % 512);
                continue;
            }

            if (isDirectoryEntry(type, entryName)) {
                File outFile = secureTarTarget(targetDir, entryName);
                if (!outFile.exists() && !outFile.mkdirs()) {
                    throw new IOException("Could not create directory: " + outFile.getAbsolutePath());
                }
            } else if (type == '2') {
                File outFile = secureTarTarget(targetDir, entryName);
                ensureParent(outFile);
                createSymlink(outFile, linkName);
            } else if (type == 0 || type == '0') {
                File outFile = secureTarTarget(targetDir, entryName);
                if (outFile.isDirectory()) {
                    skipFully(input, size);
                    skipFully(input, (512 - (size % 512)) % 512);
                    continue;
                }
                ensureParent(outFile);
                try (FileOutputStream output = new FileOutputStream(outFile, false)) {
                    copyFixedBytes(input, output, size);
                }
                applyMode(outFile, mode);
            } else {
                skipFully(input, size);
            }
            skipFully(input, (512 - (size % 512)) % 512);
        }
    }

    private boolean isMetadataOnlyEntry(char type) {
        return type == 'x' || type == 'g' || type == 'L' || type == 'K';
    }

    private boolean isRootMarkerEntry(String entryName) {
        String normalized = String.valueOf(entryName == null ? "" : entryName).replace('\\', '/').trim();
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        return normalized.isEmpty() || ".".equals(normalized) || "./".equals(normalized);
    }

    private boolean isDirectoryEntry(char type, String entryName) {
        if (type == '5') return true;
        String name = String.valueOf(entryName == null ? "" : entryName).trim();
        return !name.isEmpty() && name.endsWith("/");
    }

    private File secureTarTarget(File targetDir, String entryName) throws IOException {
        String normalized = String.valueOf(entryName == null ? "" : entryName).replace('\\', '/');
        while (normalized.startsWith("/")) normalized = normalized.substring(1);
        if (normalized.isEmpty()) {
            throw new IOException("Invalid tar entry name.");
        }
        File out = new File(targetDir, normalized);
        Path targetPath = out.getCanonicalFile().toPath();
        Path rootPath = targetDir.getCanonicalFile().toPath();
        if (!targetPath.startsWith(rootPath)) {
            throw new IOException("Blocked path traversal in tar entry: " + entryName);
        }
        return out;
    }

    private void ensureParent(File file) throws IOException {
        File parent = file.getParentFile();
        if (parent == null) return;
        if (parent.exists()) return;
        if (!parent.mkdirs()) {
            throw new IOException("Could not create parent directory: " + parent.getAbsolutePath());
        }
    }

    private void copyFixedBytes(InputStream input, FileOutputStream output, long bytes) throws IOException {
        long remaining = bytes;
        byte[] buffer = new byte[8192];
        while (remaining > 0) {
            int read = input.read(buffer, 0, (int) Math.min(buffer.length, remaining));
            if (read == -1) throw new IOException("Unexpected EOF while reading tar payload.");
            output.write(buffer, 0, read);
            remaining -= read;
        }
        output.flush();
    }

    private void applyMode(File file, int mode) {
        if ((mode & 0400) != 0) file.setReadable(true, true);
        if ((mode & 0004) != 0) file.setReadable(true, false);
        if ((mode & 0200) != 0) file.setWritable(true, true);
        if ((mode & 0002) != 0) file.setWritable(true, false);
        if ((mode & 0100) != 0 || (mode & 0010) != 0 || (mode & 0001) != 0) {
            file.setExecutable(true, false);
        }
    }

    private void createSymlink(File linkFile, String linkTarget) throws IOException {
        if (linkTarget == null || linkTarget.isBlank()) return;
        Path linkPath = linkFile.toPath();
        try {
            Files.deleteIfExists(linkPath);
            Files.createSymbolicLink(linkPath, Paths.get(linkTarget));
        } catch (UnsupportedOperationException ignored) {
            // Some Android filesystems may not support symbolic links for app users.
        }
    }

    private void clearDirectory(File directory) throws IOException {
        File[] entries = directory.listFiles();
        if (entries == null) return;
        for (File entry : entries) {
            if (entry.isDirectory()) clearDirectory(entry);
            if (!entry.delete()) {
                throw new IOException("Could not delete " + entry.getAbsolutePath());
            }
        }
    }

    private boolean readFully(InputStream input, byte[] buffer, int offset, int len) throws IOException {
        int total = 0;
        while (total < len) {
            int read = input.read(buffer, offset + total, len - total);
            if (read == -1) {
                return total != 0 && total == len;
            }
            total += read;
        }
        return true;
    }

    private boolean isZeroBlock(byte[] block) {
        for (byte b : block) {
            if (b != 0) return false;
        }
        return true;
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
        while (end < offset + len && buffer[end] != 0) end += 1;
        return new String(buffer, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    private long parseTarOctal(byte[] buffer, int offset, int len) {
        String raw = readTarString(buffer, offset, len);
        if (raw.isEmpty()) return 0L;
        try {
            return Long.parseLong(raw.trim(), 8);
        } catch (NumberFormatException ignored) {
            return 0L;
        }
    }

    private String baseName(String path) {
        if (path == null || path.isBlank()) return "";
        String normalized = path.replace('\\', '/');
        int idx = normalized.lastIndexOf('/');
        if (idx < 0) return normalized;
        return normalized.substring(idx + 1);
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

    private void copyFile(File source, File target) throws IOException {
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

    private String computeSha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
        byte[] hash = digest.digest();
        StringBuilder hex = new StringBuilder(hash.length * 2);
        for (byte value : hash) {
            String part = Integer.toHexString(0xff & value);
            if (part.length() == 1) hex.append('0');
            hex.append(part);
        }
        return hex.toString();
    }

    private String readProcessOutput(InputStream stream, int maxChars) throws IOException {
        StringBuilder out = new StringBuilder();
        boolean truncated = false;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            char[] buffer = new char[4096];
            int read;
            while ((read = reader.read(buffer)) != -1) {
                int remaining = maxChars - out.length();
                if (remaining > 0) {
                    int toAppend = Math.min(remaining, read);
                    out.append(buffer, 0, toAppend);
                }
                if (out.length() >= maxChars) {
                    truncated = true;
                }
            }
        }
        if (truncated) {
            out.append("\n[ananta-mobile-shell] Output truncated");
        }
        return out.toString().trim();
    }

    private static final class ShellExecutionResult {
        final String output;
        final int exitCode;
        final boolean timedOut;

        ShellExecutionResult(String output, int exitCode, boolean timedOut) {
            this.output = output;
            this.exitCode = exitCode;
            this.timedOut = timedOut;
        }
    }

    private static final class DistroDownloadMeta {
        final String url;
        final String sha256;

        DistroDownloadMeta(String url, String sha256) {
            this.url = url;
            this.sha256 = sha256;
        }
    }

    private static final class ProotProbeResult {
        final boolean runnable;
        final String message;

        ProotProbeResult(boolean runnable, String message) {
            this.runnable = runnable;
            this.message = String.valueOf(message == null ? "" : message).trim();
        }
    }

    private static final class ShellSessionRead {
        final String output;
        final boolean hasMore;

        ShellSessionRead(String output, boolean hasMore) {
            this.output = output;
            this.hasMore = hasMore;
        }
    }

    private static final class ShellSession {
        private final Process process;
        private final BufferedWriter stdin;
        private final StringBuilder output = new StringBuilder();
        private final Object outputLock = new Object();
        private volatile int readOffset = 0;

        ShellSession(Process process) {
            this.process = process;
            this.stdin = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
        }

        void startReaderThread() {
            Thread reader = new Thread(() -> {
                try (BufferedReader br = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
                    char[] buffer = new char[2048];
                    int read;
                    while ((read = br.read(buffer)) != -1) {
                        appendOutput(new String(buffer, 0, read));
                    }
                } catch (IOException ignored) {
                    // stream closed by process termination
                }
            }, "ananta-shell-session-reader");
            reader.setDaemon(true);
            reader.start();
        }

        void write(String input) throws IOException {
            stdin.write(input);
            stdin.flush();
        }

        ShellSessionRead readDelta(int maxChars) {
            synchronized (outputLock) {
                if (readOffset >= output.length()) {
                    return new ShellSessionRead("", false);
                }
                int available = output.length() - readOffset;
                int toRead = Math.min(available, maxChars);
                String chunk = output.substring(readOffset, readOffset + toRead);
                readOffset += toRead;
                boolean hasMore = readOffset < output.length();
                return new ShellSessionRead(chunk, hasMore);
            }
        }

        boolean isRunning() {
            return process.isAlive();
        }

        int exitCode() {
            return process.isAlive() ? -1 : process.exitValue();
        }

        void close() {
            try {
                stdin.write("exit\n");
                stdin.flush();
            } catch (IOException ignored) {
            }
            process.destroy();
            if (process.isAlive()) {
                process.destroyForcibly();
            }
        }

        private void appendOutput(String text) {
            if (text == null || text.isEmpty()) return;
            synchronized (outputLock) {
                output.append(text);
                int overflow = output.length() - MAX_SESSION_OUTPUT_CHARS;
                if (overflow > 0) {
                    output.delete(0, overflow);
                    readOffset = Math.max(0, readOffset - overflow);
                }
            }
        }
    }
}
