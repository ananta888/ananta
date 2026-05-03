package com.ananta.mobile.python;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.util.Log;

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
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.lang.reflect.Field;
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
    private static final int MAX_SHELL_TIMEOUT_SECONDS = 1800;
    private static final int MAX_SHELL_OUTPUT_CHARS = 120_000;
    private static final int MAX_SESSION_OUTPUT_CHARS = 200_000;
    private static final String PROOT_RUNTIME_SUBDIR = "proot-runtime";
    private static final String PROOT_BIN_FILE = "proot-rs";
    private static final String PROOT_CLASSIC_FILE = "proot-classic";
    private static final String PROOT_WRAPPER_FILE = "proot";
    private static final String PROOT_CLASSIC_EMBEDDED_LIB_FILE = "libprootclassic.so";
    private static final String PROOT_EMBEDDED_LIB_FILE = "libprootrs.so";
    private static final String PROOT_RS_RELEASE_URL = "https://github.com/proot-me/proot-rs/releases/download/v0.1.0/proot-rs-v0.1.0-aarch64-linux-android.tar.gz";
    private static final String PROOT_CLASSIC_RELEASE_URL = "https://github.com/proot-me/proot/releases/download/v5.3.0/proot-v5.3.0-aarch64-static";
    private static final String PROOT_DISTRO_RELEASE_API = "https://api.github.com/repos/termux/proot-distro/releases/latest";
    private static final String PROOT_DISTRO_PLUGIN_BASE = "https://raw.githubusercontent.com/termux/proot-distro/master/distro-plugins/";
    private static final String ANANTA_REPO_URL = "https://github.com/ananta888/ananta/archive/refs/heads/main.tar.gz";
    private static final String OPENCODE_VERSION = "v0.0.55";
    private static final String OPENCODE_URL = "https://github.com/opencode-ai/opencode/releases/download/" + OPENCODE_VERSION + "/opencode-linux-arm64.tar.gz";
    private static final String UBUNTU_ROOTFS_PRESEED_ASSET = "proot-seed/ubuntu-rootfs.tar.xz";
    private static final String UBUNTU_ROOTFS_PRESEED_VERSION_ASSET = "proot-seed/ubuntu-rootfs.version";
    private static final String ANANTA_WORKSPACE_PRESEED_ASSET = "proot-seed/ananta-workspace.tar.xz";
    private static final String ANANTA_WORKSPACE_PRESEED_VERSION_ASSET = "proot-seed/ananta-workspace.version";
    private static final String ROOTFS_PRESEED_MARKER_FILE = ".ananta-preseed-version";

    private static final int PROXY_PORT = 18080;

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Map<String, ShellSession> shellSessions = new ConcurrentHashMap<>();
    private final HttpConnectProxy httpProxy = new HttpConnectProxy(PROXY_PORT);
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
        result.put("proxyRunning", httpProxy.isRunning());
        result.put("proxyPort", PROXY_PORT);
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

        // Auto-start HTTP proxy for proot network access
        ensureProxyRunning();

        final String selectedShell = shell;
        final String selectedCwd = cwd;
        final String selectedInitialCommand = initialCommand;
        worker.execute(() -> {
            try {
                File workingDir = resolveShellWorkingDirectory(selectedCwd);
                ProcessBuilder builder;
                if (!selectedInitialCommand.isEmpty()) {
                    // Use -c to pass the command as argument instead of writing to stdin.
                    // This matches executeShellCommand behavior where proot works correctly.
                    builder = new ProcessBuilder("/system/bin/sh", "-c", selectedInitialCommand);
                } else {
                    builder = new ProcessBuilder(selectedShell);
                }
                builder.directory(workingDir);
                applyShellEnvironment(builder, workingDir);
                Process process = builder.redirectErrorStream(true).start();
                ShellSession session = new ShellSession(process);
                String sessionId = UUID.randomUUID().toString();
                shellSessions.put(sessionId, session);
                session.startReaderThread();

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

    private void ensureProxyRunning() {
        if (httpProxy.isRunning()) return;
        try {
            httpProxy.start();
        } catch (IOException e) {
            // Log but don't block shell session
            lastError = "HTTP proxy start failed: " + e.getMessage();
        }
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
    public void interruptShellSession(PluginCall call) {
        String sessionId = String.valueOf(call.getString("sessionId", "")).trim();
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
                session.interrupt();
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("running", session.isRunning());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Shell interrupt failed: " + error.getMessage());
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
                File embeddedClassicBinary = embeddedNativeClassicProotBinary();
                File embeddedProotBinary = embeddedNativeProotBinary();
                File selectedProotBinary = resolveProotBinaryCandidate(
                    embeddedClassicBinary,
                    embeddedProotBinary,
                    classicProotBinary,
                    runtimeProotBinary
                );
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
                result.put("prootBinarySource", resolveProotBinarySource(
                    selectedProotBinary,
                    embeddedClassicBinary,
                    embeddedProotBinary,
                    classicProotBinary
                ));
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
                File embeddedClassicBinary = embeddedNativeClassicProotBinary();
                if (isUsableBinary(embeddedClassicBinary)) {
                    createProotWrapper(binDir, embeddedClassicBinary);
                    ProotProbeResult probe = probeProotWrapper(new File(binDir, PROOT_WRAPPER_FILE));
                    JSObject result = new JSObject();
                    result.put("runtimeRoot", runtimeRoot.getAbsolutePath());
                    result.put("prootPath", new File(binDir, PROOT_WRAPPER_FILE).getAbsolutePath());
                    result.put("prootBinaryPath", embeddedClassicBinary.getAbsolutePath());
                    result.put("prootBinarySource", "embedded-classic-native-lib");
                    result.put("alreadyInstalled", true);
                    result.put("runnable", probe.runnable);
                    result.put("probeMessage", probe.message);
                    String doneMessage = probe.runnable
                        ? "Runtime bereit (APK-embedded classic proot)."
                        : "Runtime vorhanden (APK-embedded classic proot), aber nicht startbar.";
                    notifyProotProgress("runtime", "done", doneMessage, -1, -1, null);
                    call.resolve(result);
                    return;
                }

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
                File runtimeRoot = runtimeRootDir();
                File distrosDir = ensureDir(runtimeRoot, "distros");
                File distroDir = ensureDir(distrosDir, distro);
                File rootfsDir = ensureDir(distroDir, "rootfs");
                File existingRootfs = resolveInstalledRootfs(rootfsDir);
                if (existingRootfs != null) {
                    ensureDistroBootstrap(distro, runtimeRoot, existingRootfs);
                    JSObject result = new JSObject();
                    result.put("distro", distro);
                    result.put("rootfsPath", existingRootfs.getAbsolutePath());
                    result.put("alreadyInstalled", true);
                    notifyProotProgress("distro", "done", "Distro bereits installiert.", -1, -1, distro);
                    call.resolve(result);
                    return;
                }
                String bundledVersion = installBundledDistroIfAvailable(distro, rootfsDir);
                if (bundledVersion != null) {
                    File installedRootfs = resolveInstalledRootfs(rootfsDir);
                    if (installedRootfs == null) {
                        throw new IOException("Bundled distro extraction completed, but no usable rootfs was detected.");
                    }
                    writeTextFile(new File(installedRootfs, ROOTFS_PRESEED_MARKER_FILE), bundledVersion);
                    ensureDistroBootstrap(distro, runtimeRoot, installedRootfs);

                    JSObject result = new JSObject();
                    result.put("distro", distro);
                    result.put("rootfsPath", installedRootfs.getAbsolutePath());
                    result.put("source", "bundled-apk-asset");
                    result.put("preseedVersion", bundledVersion);
                    notifyProotProgress("distro", "done", "Gebuendelte Distro installiert.", -1, -1, distro);
                    call.resolve(result);
                    return;
                }
                DistroDownloadMeta downloadMeta = resolveDistroDownloadMeta(distro);
                String assetUrl = downloadMeta.url;
                if (assetUrl == null || assetUrl.isBlank()) {
                    throw new IOException("No aarch64 archive found for distro: " + distro);
                }
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
                File installedRootfs = resolveInstalledRootfs(rootfsDir);
                if (installedRootfs == null) {
                    throw new IOException("Distro extraction completed, but no usable rootfs was detected.");
                }
                ensureDistroBootstrap(distro, runtimeRoot, installedRootfs);

                JSObject result = new JSObject();
                result.put("distro", distro);
                result.put("rootfsPath", installedRootfs.getAbsolutePath());
                notifyProotProgress("distro", "done", "Distro installiert.", -1, -1, distro);
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("distro", "error", error.getMessage(), -1, -1, distro);
                call.reject("Distro installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void getGuidedSetupStatus(PluginCall call) {
        worker.execute(() -> {
            try {
                File runtimeRoot = runtimeRootDir();
                File prootWrapper = new File(runtimeRoot, "bin/" + PROOT_WRAPPER_FILE);
                File selectedBinary = resolveProotBinaryCandidate(
                    embeddedNativeClassicProotBinary(),
                    embeddedNativeProotBinary(),
                    new File(runtimeRoot, "bin/" + PROOT_CLASSIC_FILE),
                    new File(runtimeRoot, "bin/" + PROOT_BIN_FILE)
                );
                boolean runtimeInstalled = prootWrapper.exists() && selectedBinary != null && selectedBinary.exists();
                boolean runtimeReady = false;
                String runtimeMessage = "Runtime nicht installiert.";
                if (runtimeInstalled) {
                    createProotWrapper(new File(runtimeRoot, "bin"), selectedBinary);
                    ProotProbeResult probe = probeProotWrapper(prootWrapper);
                    runtimeReady = probe.runnable;
                    runtimeMessage = probe.message;
                }

                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                boolean ubuntuInstalled = ubuntuRootfs != null;
                boolean pythonReady = false;
                boolean pipReady = false;
                boolean libgompReady = false;
                boolean opencodeReady = false;
                boolean anantaCliReady = false;
                boolean anantaTuiReady = false;
                boolean workerCommandReady = false;
                boolean workerImportReady = false;
                String workerProbeMessage = "";

                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                boolean workspaceInstalled = new File(workspaceRoot, "agent/ai_agent.py").isFile();

                if (runtimeReady && ubuntuInstalled) {
                    pythonReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v python3 >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    pipReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v pip3 >/dev/null 2>&1 || python3 -m pip --version >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    libgompReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if [ -f /lib/aarch64-linux-gnu/libgomp.so.1 ] || [ -f /usr/lib/aarch64-linux-gnu/libgomp.so.1 ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    opencodeReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v opencode >/dev/null 2>&1; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    anantaCliReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta >/dev/null 2>&1 || [ -x /usr/local/bin/ananta ] || [ -x /home/ananta/.local/bin/ananta ] || [ -x /root/.local/bin/ananta ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");
                    anantaTuiReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta >/dev/null 2>&1; then ananta tui --help >/dev/null 2>&1 && echo ANANTA_OK || echo ANANTA_MISSING; elif [ -x /usr/local/bin/ananta ]; then /usr/local/bin/ananta tui --help >/dev/null 2>&1 && echo ANANTA_OK || echo ANANTA_MISSING; else echo ANANTA_MISSING; fi");
                    workerCommandReady = probeInProot(runtimeRoot, ubuntuRootfs,
                        "if command -v ananta-worker >/dev/null 2>&1 || [ -x /usr/local/bin/ananta-worker ] || [ -x /home/ananta/.local/bin/ananta-worker ] || [ -x /root/.local/bin/ananta-worker ]; then echo ANANTA_OK; else echo ANANTA_MISSING; fi");

                    if (workspaceInstalled) {
                        File dataRoot = new File(getContext().getFilesDir(), "ananta-data");
                        String importCmd = "cd " + shQuote(workspaceRoot.getAbsolutePath())
                            + " && if [ -f ./agent/ai_agent.py ]; then PYTHONPATH="
                            + shQuote(workspaceRoot.getAbsolutePath())
                            + " DATA_DIR=" + shQuote(dataRoot.getAbsolutePath())
                            + " python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_IMPORT_OK')\"; else echo ANANTA_WORKER_MISSING; exit 3; fi";
                        ShellExecutionResult importProbe = runInProot(runtimeRoot, ubuntuRootfs, importCmd, 120);
                        String out = String.valueOf(importProbe.output == null ? "" : importProbe.output);
                        workerImportReady = !importProbe.timedOut && importProbe.exitCode == 0 && out.contains("ANANTA_WORKER_IMPORT_OK");
                        workerProbeMessage = out.trim();
                    }
                }

                JSObject result = new JSObject();
                result.put("runtimeInstalled", runtimeInstalled);
                result.put("runtimeReady", runtimeReady);
                result.put("runtimeMessage", runtimeMessage);
                result.put("ubuntuInstalled", ubuntuInstalled);
                result.put("pythonReady", pythonReady);
                result.put("pipReady", pipReady);
                result.put("libgompReady", libgompReady);
                result.put("opencodeReady", opencodeReady);
                result.put("anantaCliReady", anantaCliReady);
                result.put("anantaTuiReady", anantaTuiReady);
                result.put("workerCommandReady", workerCommandReady);
                result.put("workspaceInstalled", workspaceInstalled);
                result.put("workerImportReady", workerImportReady);
                result.put("workerProbeMessage", workerProbeMessage);
                result.put("workspacePath", workspaceRoot.getAbsolutePath());
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                call.reject("Could not read guided setup status: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installAnantaWorkspace(PluginCall call) {
        worker.execute(() -> {
            try {
                String repoUrl = String.valueOf(call.getString("repoUrl", ANANTA_REPO_URL)).trim();
                if (repoUrl.isEmpty()) repoUrl = ANANTA_REPO_URL;
                notifyProotProgress("workspace", "preparing", "Ananta-Workspace Installation gestartet.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                String bundledVersion = installBundledWorkspaceIfAvailable(workspaceRoot);
                if (bundledVersion != null) {
                    File marker = new File(workspaceRoot, "agent/ai_agent.py");
                    if (!marker.isFile()) {
                        throw new IOException("Bundled workspace extraction incomplete: agent/ai_agent.py not found.");
                    }
                    ensureWorkspaceWorkerDependenciesIfPossible(runtimeRoot, workspaceRoot);
                    JSObject result = new JSObject();
                    result.put("workspacePath", workspaceRoot.getAbsolutePath());
                    result.put("repoUrl", "apk-asset:" + ANANTA_WORKSPACE_PRESEED_ASSET);
                    result.put("source", "bundled-apk-asset");
                    result.put("preseedVersion", bundledVersion);
                    notifyProotProgress("workspace", "done", "Gebuendelter Workspace installiert.", -1, -1, "ubuntu");
                    call.resolve(result);
                    return;
                }

                File tmpDir = ensureDir(runtimeRoot, "tmp");
                File archive = new File(tmpDir, "ananta-workspace.tar.gz");
                downloadToFile(repoUrl, archive, "workspace", "ubuntu");
                notifyProotProgress("workspace", "extracting", "Workspace wird entpackt.", -1, -1, "ubuntu");

                if (workspaceRoot.exists()) {
                    clearDirectory(workspaceRoot);
                } else if (!workspaceRoot.mkdirs()) {
                    throw new IOException("Could not create workspace directory: " + workspaceRoot.getAbsolutePath());
                }
                extractTarGzToDirectory(archive, workspaceRoot);
                File marker = new File(workspaceRoot, "agent/ai_agent.py");
                if (!marker.isFile()) {
                    throw new IOException("Workspace extraction incomplete: agent/ai_agent.py not found.");
                }
                ensureWorkspaceWorkerDependenciesIfPossible(runtimeRoot, workspaceRoot);
                JSObject result = new JSObject();
                result.put("workspacePath", workspaceRoot.getAbsolutePath());
                result.put("repoUrl", repoUrl);
                notifyProotProgress("workspace", "done", "Ananta-Workspace installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("workspace", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("Workspace installation failed: " + error.getMessage());
            }
        });
    }

    @PluginMethod
    public void installWorkerDependencies(PluginCall call) {
        worker.execute(() -> {
            try {
                notifyProotProgress("worker", "preparing", "Worker-Dependencies werden installiert.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                if (ubuntuRootfs == null) {
                    throw new IOException("Ubuntu rootfs fehlt. Bitte zuerst Distro installieren.");
                }
                File workspaceRoot = new File(getContext().getFilesDir(), "ananta");
                File marker = new File(workspaceRoot, "agent/ai_agent.py");
                if (!marker.isFile()) {
                    throw new IOException("Workspace fehlt. Bitte zuerst installAnantaWorkspace ausfuehren.");
                }

                if (probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
                    JSObject result = new JSObject();
                    result.put("ok", true);
                    result.put("message", "Worker-Dependencies bereits vorhanden.");
                    result.put("source", "bundled-or-existing");
                    notifyProotProgress("worker", "done", "Worker-Dependencies bereits vorhanden.", -1, -1, "ubuntu");
                    call.resolve(result);
                    return;
                }

                notifyProotProgress("worker", "installing", "Fehlende Worker-Dependencies werden installiert.", -1, -1, "ubuntu");
                ShellExecutionResult install = runInProot(
                    runtimeRoot,
                    ubuntuRootfs,
                    buildWorkerDependencyInstallCommand(workspaceRoot, new File(getContext().getFilesDir(), "ananta-data")),
                    1200
                );
                String output = String.valueOf(install.output == null ? "" : install.output);
                if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_WORKER_DEPS_OK")) {
                    throw new IOException("Worker dependency install failed: " + output.trim());
                }
                if (!probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
                    throw new IOException("Worker dependency verification failed after install.");
                }
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("message", "Worker-Dependencies installiert.");
                result.put("source", "installed-in-proot");
                notifyProotProgress("worker", "done", "Worker-Dependencies installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("worker", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("Worker dependency installation failed: " + error.getMessage());
            }
        });
    }

    private void ensureWorkspaceWorkerDependenciesIfPossible(File runtimeRoot, File workspaceRoot) throws Exception {
        if (runtimeRoot == null || workspaceRoot == null) return;
        if (!new File(workspaceRoot, "agent/ai_agent.py").isFile()) return;
        File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
        if (ubuntuRootfs == null) return;
        if (probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) return;
        notifyProotProgress("worker", "installing", "Fehlende Worker-Dependencies werden fuer Workspace installiert.", -1, -1, "ubuntu");
        ShellExecutionResult install = runInProot(
            runtimeRoot,
            ubuntuRootfs,
            buildWorkerDependencyInstallCommand(workspaceRoot, new File(getContext().getFilesDir(), "ananta-data")),
            1200
        );
        String output = String.valueOf(install.output == null ? "" : install.output);
        if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_WORKER_DEPS_OK")) {
            throw new IOException("Worker dependency install failed: " + output.trim());
        }
        if (!probeWorkerDependenciesReady(runtimeRoot, ubuntuRootfs, workspaceRoot)) {
            throw new IOException("Worker dependency verification failed after install.");
        }
    }

    private boolean probeWorkerDependenciesReady(File runtimeRoot, File ubuntuRootfs, File workspaceRoot) throws Exception {
        if (runtimeRoot == null || ubuntuRootfs == null || workspaceRoot == null) return false;
        if (!new File(workspaceRoot, "agent/ai_agent.py").isFile()) return false;
        File dataRoot = new File(getContext().getFilesDir(), "ananta-data");
        ShellExecutionResult probe = runInProot(
            runtimeRoot,
            ubuntuRootfs,
            ""
                + "ANANTA_WORKSPACE=" + shQuote(workspaceRoot.getAbsolutePath()) + "; "
                + "ANANTA_DATA_DIR=" + shQuote(dataRoot.getAbsolutePath()) + "; "
                + "mkdir -p \"$ANANTA_DATA_DIR\"; "
                + "if ! command -v python3 >/dev/null 2>&1; then echo ANANTA_MISSING_PYTHON; exit 2; fi; "
                + "if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then echo ANANTA_MISSING_PIP; exit 3; fi; "
                + "if ! command -v ananta-worker >/dev/null 2>&1 && [ ! -x /usr/local/bin/ananta-worker ] && [ ! -x /home/ananta/.local/bin/ananta-worker ] && [ ! -x /root/.local/bin/ananta-worker ]; then echo ANANTA_MISSING_WORKER_CMD; exit 5; fi; "
                + "DATA_DIR=\"$ANANTA_DATA_DIR\" PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_IMPORT_OK')\"; "
                + "echo ANANTA_WORKER_DEPS_READY",
            300
        );
        String output = String.valueOf(probe.output == null ? "" : probe.output);
        return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_WORKER_DEPS_READY") && output.contains("ANANTA_WORKER_IMPORT_OK");
    }

    private String buildWorkerDependencyInstallCommand(File workspaceRoot, File dataRoot) {
        String workspacePath = workspaceRoot.getAbsolutePath();
        String dataPath = dataRoot.getAbsolutePath();
        return ""
            + "set -e; "
            + "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; "
            + "rm -f /etc/apt/apt.conf.d/99proxy 2>/dev/null || true; "
            + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
            + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
            + "fi; "
            + "export DEBIAN_FRONTEND=noninteractive; "
            + "MISSING_PACKAGES=''; "
            + "for bin in python3 git curl tar; do command -v \"$bin\" >/dev/null 2>&1 || MISSING_PACKAGES=\"$MISSING_PACKAGES $bin\"; done; "
            + "if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then MISSING_PACKAGES=\"$MISSING_PACKAGES python3-pip\"; fi; "
            + "if [ -n \"$MISSING_PACKAGES\" ]; then "
            + "if command -v apt-get >/dev/null 2>&1; then "
            + "apt-get update; "
            + "apt-get install -y --no-install-recommends ca-certificates $MISSING_PACKAGES; "
            + "else echo ANANTA_APT_MISSING:$MISSING_PACKAGES; exit 2; fi; "
            + "fi; "
            + "ANANTA_WORKSPACE=" + shQuote(workspacePath) + "; "
            + "ANANTA_DATA_DIR=" + shQuote(dataPath) + "; "
            + "mkdir -p \"$ANANTA_DATA_DIR\"; "
            + "if [ ! -f \"$ANANTA_WORKSPACE/pyproject.toml\" ]; then echo ANANTA_WORKSPACE_MISSING; exit 4; fi; "
            + "python3 -m pip install --break-system-packages --ignore-installed --no-input --progress-bar off "
            + "flask requests flask-cors pydantic pydantic-settings python-dotenv prometheus-client pyjwt "
            + "portalocker psutil sqlmodel typer click gitpython flask-sock simple-websocket "
            + "pypdf python-docx openpyxl python-pptx; "
            + "python3 -m pip install --break-system-packages --ignore-installed --no-input --progress-bar off -e \"$ANANTA_WORKSPACE\"; "
            + "export PATH=\"/home/ananta/.local/bin:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH\"; "
            + "if ! command -v ananta >/dev/null 2>&1; then "
            + "mkdir -p /usr/local/bin /home/ananta/.local/bin /root/.local/bin 2>/dev/null || true; "
            + "for target in /usr/local/bin/ananta /home/ananta/.local/bin/ananta /root/.local/bin/ananta; do "
            + "cat >\"$target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "ANANTA_WORKSPACE=" + workspacePath + "\n"
            + "DATA_DIR=${DATA_DIR:-" + dataPath + "}\n"
            + "export DATA_DIR\n"
            + "PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" exec python3 -m agent.cli.main \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$target\" 2>/dev/null || true; "
            + "done; "
            + "fi; "
            + "if ! command -v ananta >/dev/null 2>&1 && [ -x /usr/local/bin/ananta ]; then ln -sf /usr/local/bin/ananta /usr/bin/ananta 2>/dev/null || true; fi; "
            + "if ! command -v ananta >/dev/null 2>&1; then "
            + "if [ -x /usr/local/bin/ananta ]; then ANANTA_CLI=/usr/local/bin/ananta; "
            + "elif [ -x /home/ananta/.local/bin/ananta ]; then ANANTA_CLI=/home/ananta/.local/bin/ananta; "
            + "elif [ -x /root/.local/bin/ananta ]; then ANANTA_CLI=/root/.local/bin/ananta; "
            + "else echo ANANTA_CLI_MISSING; exit 5; fi; "
            + "else ANANTA_CLI=$(command -v ananta); fi; "
            + "for worker_target in /usr/local/bin/ananta-worker /home/ananta/.local/bin/ananta-worker /root/.local/bin/ananta-worker; do "
            + "cat >\"$worker_target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "ANANTA_WORKSPACE=" + workspacePath + "\n"
            + "export ROLE=${ROLE:-worker}\n"
            + "export AGENT_NAME=${AGENT_NAME:-android-worker}\n"
            + "export PORT=${PORT:-5001}\n"
            + "export HUB_URL=${HUB_URL:-http://127.0.0.1:5000}\n"
            + "export AGENT_URL=${AGENT_URL:-http://127.0.0.1:${PORT}}\n"
            + "export DATA_DIR=${DATA_DIR:-" + dataPath + "}\n"
            + "PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" exec python3 -m agent.ai_agent \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$worker_target\" 2>/dev/null || true; "
            + "done; "
            + "for tui_target in /usr/local/bin/ananta-tui /home/ananta/.local/bin/ananta-tui /root/.local/bin/ananta-tui; do "
            + "cat >\"$tui_target\" <<'EOF'\n"
            + "#!/bin/sh\n"
            + "exec ananta tui \"$@\"\n"
            + "EOF\n"
            + "chmod 755 \"$tui_target\" 2>/dev/null || true; "
            + "done; "
            + "\"$ANANTA_CLI\" --help >/dev/null 2>&1; "
            + "\"$ANANTA_CLI\" tui --help >/dev/null 2>&1; "
            + "if ! command -v ananta-worker >/dev/null 2>&1 && [ ! -x /usr/local/bin/ananta-worker ] && [ ! -x /home/ananta/.local/bin/ananta-worker ] && [ ! -x /root/.local/bin/ananta-worker ]; then echo ANANTA_WORKER_CMD_MISSING; exit 6; fi; "
            + "DATA_DIR=\"$ANANTA_DATA_DIR\" PYTHONPATH=\"$ANANTA_WORKSPACE:${PYTHONPATH:-}\" python3 -c \"from agent.ai_agent import create_app; print('ANANTA_WORKER_DEPS_OK')\"";
    }

    @PluginMethod
    public void installOpencode(PluginCall call) {
        worker.execute(() -> {
            try {
                notifyProotProgress("opencode", "preparing", "opencode wird installiert.", -1, -1, "ubuntu");
                File runtimeRoot = runtimeRootDir();
                File ubuntuRootfs = resolveInstalledRootfs(new File(new File(runtimeRoot, "distros/ubuntu"), "rootfs"));
                if (ubuntuRootfs == null) {
                    throw new IOException("Ubuntu rootfs fehlt. Bitte zuerst Distro installieren.");
                }
                String url = String.valueOf(call.getString("opencodeUrl", OPENCODE_URL)).trim();
                if (url.isEmpty()) url = OPENCODE_URL;
                String cmd = ""
                    + "set -e; "
                    + "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; rm -f /etc/apt/apt.conf.d/99proxy 2>/dev/null || true; "
                    + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
                    + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
                    + "fi; "
                    + "export DEBIAN_FRONTEND=noninteractive; "
                    + "if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then "
                    + "if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tar; "
                    + "else echo ANANTA_APT_MISSING; exit 2; fi; "
                    + "fi; "
                    + "TMP_TGZ=/tmp/opencode-linux-arm64.tar.gz; TMP_DIR=/tmp/opencode-install; rm -rf \"$TMP_DIR\"; mkdir -p \"$TMP_DIR\"; "
                    + "curl -L --fail --connect-timeout 20 --max-time 600 " + shQuote(url) + " -o \"$TMP_TGZ\"; "
                    + "tar xzf \"$TMP_TGZ\" -C \"$TMP_DIR\"; "
                    + "if [ -f \"$TMP_DIR/opencode\" ]; then install -m 0755 \"$TMP_DIR/opencode\" /usr/local/bin/opencode; "
                    + "elif [ -f \"$TMP_DIR/bin/opencode\" ]; then install -m 0755 \"$TMP_DIR/bin/opencode\" /usr/local/bin/opencode; "
                    + "else CAND=$(find \"$TMP_DIR\" -type f -name opencode | head -n 1); [ -n \"$CAND\" ] && install -m 0755 \"$CAND\" /usr/local/bin/opencode || { echo ANANTA_OPENCODE_BIN_MISSING; exit 3; }; fi; "
                    + "opencode --version; echo ANANTA_OPENCODE_OK";
                ShellExecutionResult install = runInProot(runtimeRoot, ubuntuRootfs, cmd, 1200);
                String output = String.valueOf(install.output == null ? "" : install.output);
                if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_OPENCODE_OK")) {
                    throw new IOException("opencode install failed: " + output.trim());
                }
                JSObject result = new JSObject();
                result.put("ok", true);
                result.put("version", OPENCODE_VERSION);
                result.put("output", output);
                notifyProotProgress("opencode", "done", "opencode installiert.", -1, -1, "ubuntu");
                call.resolve(result);
            } catch (Exception error) {
                lastError = error.getMessage();
                notifyProotProgress("opencode", "error", error.getMessage(), -1, -1, "ubuntu");
                call.reject("opencode installation failed: " + error.getMessage());
            }
        });
    }

    @Override
    public void load() {
        super.load();
        Log.i("AnantaProxy", "PythonRuntimePlugin loaded, starting HTTP proxy on port " + PROXY_PORT);
        ensureProxyRunning();
        Log.i("AnantaProxy", "Proxy running: " + httpProxy.isRunning());
    }

    @Override
    protected void handleOnDestroy() {
        httpProxy.stop();
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
        ProcessBuilder builder = new ProcessBuilder("/system/bin/sh", "-lc", command);
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
        // Prevent Chaquopy's Python 3.11 env from leaking into proot sessions running Python 3.13
        env.remove("PYTHONPATH");
        env.remove("PYTHONHOME");
        env.remove("PYTHONDONTWRITEBYTECODE");
        env.remove("PYTHONSTARTUP");
        String path = workingDir.getAbsolutePath();
        env.put("HOME", path);
        env.put("PWD", path);
        env.put("ANANTA_MOBILE_FILES", path);
        File runtimeRoot = runtimeRootDir();
        File prootBin = new File(runtimeRoot, "bin");
        String existingPath = String.valueOf(env.getOrDefault("PATH", ""));
        env.put("PATH", prootBin.getAbsolutePath() + (existingPath.isEmpty() ? "" : ":" + existingPath));
        env.putIfAbsent("TERM", "xterm-256color");
        env.put("PROOT_FORCE_KOMPAT", "1");
        env.put("GLIBC_TUNABLES", "glibc.pthread.rseq=0");
        File prootTmp = new File(runtimeRoot, "tmp");
        if (!prootTmp.exists()) prootTmp.mkdirs();
        env.put("PROOT_TMP_DIR", prootTmp.getAbsolutePath());
        // Resolve proot-loader from APK native libs — required for execve under untrusted_app SELinux domain
        String prootLoaderPath = resolveNativeLibPath("libproot-loader.so");
        if (prootLoaderPath != null) {
            env.put("PROOT_LOADER", prootLoaderPath);
        }
        ensureProotLoaderSymlink();
        // libtalloc.so is needed by Termux-forked proot; ensure it's findable
        String nativeLibDir = resolveNativeLibPath("libprootclassic.so");
        if (nativeLibDir != null) {
            String nativeDir = new File(nativeLibDir).getParent();
            String ldPath = env.getOrDefault("LD_LIBRARY_PATH", "");
            if (ldPath.isEmpty()) {
                env.put("LD_LIBRARY_PATH", nativeDir);
            } else if (!ldPath.contains(nativeDir)) {
                env.put("LD_LIBRARY_PATH", nativeDir + ":" + ldPath);
            }
        }
        // HTTP proxy for proot external network access
        if (httpProxy.isRunning()) {
            String proxyUrl = "http://127.0.0.1:" + PROXY_PORT;
            env.put("http_proxy", proxyUrl);
            env.put("https_proxy", proxyUrl);
            env.put("HTTP_PROXY", proxyUrl);
            env.put("HTTPS_PROXY", proxyUrl);
        }
    }

    private File runtimeRootDir() {
        return new File(getContext().getFilesDir(), PROOT_RUNTIME_SUBDIR);
    }

    private String resolveNativeLibPath(String libName) {
        Context context = getContext();
        if (context == null) return null;
        ApplicationInfo appInfo = context.getApplicationInfo();
        if (appInfo == null || appInfo.nativeLibraryDir == null) return null;
        File lib = new File(appInfo.nativeLibraryDir, libName);
        return lib.isFile() ? lib.getAbsolutePath() : null;
    }

    /**
     * Ensures a symlink at /data/data/com.ananta.mobile/ldr/<libName> pointing
     * to the actual native lib in the APK directory. Required because proot
     * has a hardcoded loader path that must resolve at runtime.
     */
    private void ensureProotLoaderSymlink() {
        Context context = getContext();
        if (context == null) return;
        try {
            String loaderSrc = resolveNativeLibPath("libproot-loader.so");
            if (loaderSrc == null) return;
            File ldrDir = new File(context.getDataDir(), "ldr");
            if (!ldrDir.exists()) ldrDir.mkdirs();
            File symlinkFile = new File(ldrDir, "libproot-loader.so");
            Path symlinkPath = symlinkFile.toPath();
            if (Files.isSymbolicLink(symlinkPath)) {
                String existing = Files.readSymbolicLink(symlinkPath).toString();
                if (existing.equals(loaderSrc)) return;
                Files.delete(symlinkPath);
            } else if (symlinkFile.exists()) {
                symlinkFile.delete();
            }
            Files.createSymbolicLink(symlinkPath, Paths.get(loaderSrc));
        } catch (Exception e) {
            // Loader symlink creation failed — proot will fall back to embedded loader
        }
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
        File selectedBinary = resolveProotBinaryCandidate(
            embeddedNativeClassicProotBinary(),
            embeddedNativeProotBinary(),
            classicBinary,
            runtimeBinary
        );
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

    private File embeddedNativeClassicProotBinary() {
        Context context = getContext();
        if (context == null || context.getApplicationInfo() == null) return null;
        String nativeDir = String.valueOf(context.getApplicationInfo().nativeLibraryDir == null ? "" : context.getApplicationInfo().nativeLibraryDir).trim();
        if (nativeDir.isEmpty()) return null;
        File candidate = new File(nativeDir, PROOT_CLASSIC_EMBEDDED_LIB_FILE);
        return candidate.exists() ? candidate : null;
    }

    private File resolveProotBinaryCandidate(File embeddedClassicBinary, File embeddedBinary, File classicBinary, File runtimeBinary) {
        if (isUsableBinary(embeddedClassicBinary)) return embeddedClassicBinary;
        if (isUsableBinary(embeddedBinary)) return embeddedBinary;
        if (isUsableBinary(classicBinary)) return classicBinary;
        if (isUsableBinary(runtimeBinary)) return runtimeBinary;
        return null;
    }

    private String resolveProotBinarySource(File selected, File embeddedClassic, File embeddedRs, File classicRuntime) {
        if (isEmbeddedBinary(selected, embeddedClassic)) return "embedded-classic-native-lib";
        if (isEmbeddedBinary(selected, embeddedRs)) return "embedded-native-lib";
        if (selected != null && classicRuntime != null) {
            try {
                if (selected.getCanonicalPath().equals(classicRuntime.getCanonicalPath())) {
                    return "classic-runtime-files";
                }
            } catch (IOException ignored) {
                if (selected.getAbsolutePath().equals(classicRuntime.getAbsolutePath())) return "classic-runtime-files";
            }
        }
        return "runtime-files";
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

    private void extractTarGzToDirectory(File archive, File targetDir) throws IOException {
        Process process = null;
        try {
            process = new ProcessBuilder(
                "/system/bin/tar",
                "xzf",
                archive.getAbsolutePath(),
                "-C",
                targetDir.getAbsolutePath(),
                "--strip-components=1"
            )
                .redirectErrorStream(true)
                .start();
            boolean finished = process.waitFor(240, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new IOException("tar extraction timeout");
            }
            String output = readProcessOutput(process.getInputStream(), 8_000);
            if (process.exitValue() != 0) {
                throw new IOException(output.isBlank() ? "tar extraction failed with exit " + process.exitValue() : output);
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IOException("tar extraction interrupted", interrupted);
        } finally {
            if (process != null) process.destroy();
        }
    }

    private void extractTarXzToDirectory(File archive, File targetDir) throws IOException {
        try (InputStream fis = new FileInputStream(archive);
             InputStream xis = new XZInputStream(fis)) {
            String systemTarError = extractTarStreamWithSystemTar(xis, targetDir);
            if (systemTarError == null) {
                return;
            }
            try (InputStream fallbackFis = new FileInputStream(archive);
                 InputStream fallbackXis = new XZInputStream(fallbackFis);
                 BufferedInputStream input = new BufferedInputStream(fallbackXis)) {
                extractTarStreamToDirectory(input, targetDir);
            } catch (IOException parseError) {
                throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
            }
        } catch (IOException parseError) {
            throw parseError;
        }
    }

    private String installBundledDistroIfAvailable(String distro, File rootfsDir) throws IOException {
        if (!"ubuntu".equals(distro) || !assetExists(UBUNTU_ROOTFS_PRESEED_ASSET)) {
            return null;
        }

        notifyProotProgress("distro", "extracting", "Gebuendelte Ubuntu-Distro wird entpackt.", -1, -1, distro);
        clearDirectory(rootfsDir);
        try {
            extractTarXzAssetToDirectory(UBUNTU_ROOTFS_PRESEED_ASSET, rootfsDir);
        } catch (IOException error) {
            clearDirectory(rootfsDir);
            throw error;
        }

        String version = readAssetTextIfExists(UBUNTU_ROOTFS_PRESEED_VERSION_ASSET);
        if (version == null || version.isBlank()) {
            version = "bundled-ubuntu-rootfs";
        }
        return version.trim();
    }

    private String installBundledWorkspaceIfAvailable(File workspaceRoot) throws IOException {
        if (!assetExists(ANANTA_WORKSPACE_PRESEED_ASSET)) {
            return null;
        }

        notifyProotProgress("workspace", "extracting", "Gebuendelter Workspace wird entpackt.", -1, -1, "ubuntu");
        if (workspaceRoot.exists()) {
            clearDirectory(workspaceRoot);
        } else if (!workspaceRoot.mkdirs()) {
            throw new IOException("Could not create workspace directory: " + workspaceRoot.getAbsolutePath());
        }

        try {
            extractTarXzAssetToDirectory(ANANTA_WORKSPACE_PRESEED_ASSET, workspaceRoot);
        } catch (IOException error) {
            clearDirectory(workspaceRoot);
            throw error;
        }

        String version = readAssetTextIfExists(ANANTA_WORKSPACE_PRESEED_VERSION_ASSET);
        if (version == null || version.isBlank()) {
            version = "bundled-ananta-workspace";
        }
        return version.trim();
    }

    private void extractTarXzAssetToDirectory(String assetPath, File targetDir) throws IOException {
        String systemTarError;
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream xzInput = new XZInputStream(assetInput)) {
            systemTarError = extractTarStreamWithSystemTar(xzInput, targetDir, 900);
            if (systemTarError == null) {
                return;
            }
        }

        clearDirectory(targetDir);
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream xzInput = new XZInputStream(assetInput);
             BufferedInputStream input = new BufferedInputStream(xzInput)) {
            extractTarStreamToDirectory(input, targetDir);
        } catch (IOException parseError) {
            throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
        }
    }

    private void extractTarGzAssetToDirectory(String assetPath, File targetDir) throws IOException {
        String systemTarError;
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream gzipInput = new GZIPInputStream(assetInput)) {
            systemTarError = extractTarStreamWithSystemTar(gzipInput, targetDir, 300);
            if (systemTarError == null) {
                return;
            }
        }

        clearDirectory(targetDir);
        try (InputStream assetInput = new BufferedInputStream(getContext().getAssets().open(assetPath));
             InputStream gzipInput = new GZIPInputStream(assetInput);
             BufferedInputStream input = new BufferedInputStream(gzipInput)) {
            extractTarStreamToDirectory(input, targetDir);
        } catch (IOException parseError) {
            throw new IOException(parseError.getMessage() + " | system tar: " + systemTarError, parseError);
        }
    }

    private boolean assetExists(String assetPath) {
        Context context = getContext();
        if (context == null) return false;
        try (InputStream ignored = context.getAssets().open(assetPath)) {
            return true;
        } catch (IOException ignored) {
            return false;
        }
    }

    private String readAssetTextIfExists(String assetPath) throws IOException {
        Context context = getContext();
        if (context == null || !assetExists(assetPath)) return null;
        try (InputStream input = context.getAssets().open(assetPath)) {
            return readProcessOutput(input, 4_000).trim();
        }
    }

    private void writeTextFile(File file, String content) throws IOException {
        ensureParent(file);
        try (FileOutputStream output = new FileOutputStream(file, false)) {
            output.write(String.valueOf(content == null ? "" : content).getBytes(StandardCharsets.UTF_8));
            output.flush();
        }
    }

    private String extractTarStreamWithSystemTar(InputStream tarStream, File targetDir) {
        return extractTarStreamWithSystemTar(tarStream, targetDir, 240);
    }

    private String extractTarStreamWithSystemTar(InputStream tarStream, File targetDir, int timeoutSeconds) {
        Process process = null;
        try {
            process = new ProcessBuilder(
                "/system/bin/tar",
                "-xf",
                "-",
                "-C",
                targetDir.getAbsolutePath()
            )
                .redirectErrorStream(true)
                .start();
            try (OutputStream processInput = process.getOutputStream()) {
                copyStream(tarStream, processInput);
            }
            boolean finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                return "timeout";
            }
            String output = readProcessOutput(process.getInputStream(), 8_000);
            if (process.exitValue() == 0) return null;
            return output.isBlank() ? "exit " + process.exitValue() : output;
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage() == null ? "" : error.getMessage()).trim();
            if (message.isEmpty()) message = error.getClass().getSimpleName();
            return message;
        } finally {
            if (process != null) process.destroy();
        }
    }

    private File resolveInstalledRootfs(File rootfsDir) {
        if (rootfsDir == null || !rootfsDir.isDirectory()) return null;
        if (resolveLoginShellPath(rootfsDir) != null) return rootfsDir;
        File[] children = rootfsDir.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (!child.isDirectory()) continue;
            if (resolveLoginShellPath(child) != null) return child;
        }
        return null;
    }

    private void ensureDistroBootstrap(String distro, File runtimeRoot, File rootfsDir) throws Exception {
        if (!requiresPythonBootstrap(distro)) return;
        if (distroHasPython(runtimeRoot, rootfsDir)) return;
        notifyProotProgress("distro", "extracting", "Installiere Python in Distro.", -1, -1, distro);
        ShellExecutionResult install = runInProot(
            runtimeRoot,
            rootfsDir,
            "set -e; "
                + "if [ ! -s /etc/resolv.conf ] || ! grep -Eq '^nameserver[[:space:]]+' /etc/resolv.conf 2>/dev/null; then "
                + "printf 'nameserver 1.1.1.1\\nnameserver 8.8.8.8\\n' > /etc/resolv.conf 2>/dev/null || true; "
                + "fi; "
                + "if command -v apt-get >/dev/null 2>&1; then export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y python3; else echo ANANTA_APT_MISSING; exit 2; fi; "
                + "if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then echo ANANTA_PY_OK; else echo ANANTA_PY_MISSING; exit 3; fi",
            600
        );
        String output = String.valueOf(install.output == null ? "" : install.output);
        if (install.timedOut || install.exitCode != 0 || !output.contains("ANANTA_PY_OK")) {
            throw new IOException("Python bootstrap failed: " + output.trim());
        }
    }

    private boolean requiresPythonBootstrap(String distro) {
        String normalized = String.valueOf(distro == null ? "" : distro).trim().toLowerCase();
        return "ubuntu".equals(normalized) || "debian".equals(normalized);
    }

    private boolean distroHasPython(File runtimeRoot, File rootfsDir) throws Exception {
        ShellExecutionResult probe = runInProot(
            runtimeRoot,
            rootfsDir,
            "if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then echo ANANTA_PY_OK; else echo ANANTA_PY_MISSING; fi",
            120
        );
        String output = String.valueOf(probe.output == null ? "" : probe.output);
        return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_PY_OK");
    }

    private boolean probeInProot(File runtimeRoot, File rootfsDir, String command) {
        try {
            ShellExecutionResult probe = runInProot(runtimeRoot, rootfsDir, command, 180);
            String output = String.valueOf(probe.output == null ? "" : probe.output);
            return !probe.timedOut && probe.exitCode == 0 && output.contains("ANANTA_OK");
        } catch (Exception ignored) {
            return false;
        }
    }

    private ShellExecutionResult runInProot(File runtimeRoot, File rootfsDir, String innerCommand, int timeoutSeconds) throws Exception {
        String runtimePath = runtimeRoot.getAbsolutePath();
        String rootfsPath = rootfsDir.getAbsolutePath();
        String wrappedInnerCommand =
            "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; "
                + "export HOME=/root; "
                + "export TERM=xterm-256color; "
                + String.valueOf(innerCommand == null ? "" : innerCommand).trim();
        String command = ""
            + "ANANTA_PROOT_RUNTIME=" + shQuote(runtimePath) + "; "
            + "ANANTA_ROOTFS=" + shQuote(rootfsPath) + "; "
            + "ANANTA_PROOT_WRAPPER=\"$ANANTA_PROOT_RUNTIME/bin/proot\"; "
            + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; "
            + "mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
            + "PROOT_FORCE_KOMPAT=1 GLIBC_TUNABLES=glibc.pthread.rseq=0 "
            + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color "
            + "/system/bin/sh \"$ANANTA_PROOT_WRAPPER\" "
            + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" "
            + "-w / /bin/sh -c " + shQuote(wrappedInnerCommand);
        return executeShellCommand(command, timeoutSeconds);
    }

    private String shQuote(String value) {
        String text = String.valueOf(value == null ? "" : value);
        return "'" + text.replace("'", "'\"'\"'") + "'";
    }

    private String resolveLoginShellPath(File rootfsDir) {
        if (rootfsDir == null || !rootfsDir.isDirectory()) return null;
        String[] candidates = {
            "/usr/bin/bash", "/usr/bin/dash", "/usr/bin/sh",
            "/bin/bash", "/bin/sh", "/bin/dash", "/bin/ash"
        };
        for (String candidate : candidates) {
            File file = new File(rootfsDir, candidate.startsWith("/") ? candidate.substring(1) : candidate);
            if (file.isFile()) return candidate;
        }
        return null;
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

    private void copyStream(InputStream input, OutputStream output) throws IOException {
        byte[] buffer = new byte[8192];
        int read;
        while ((read = input.read(buffer)) != -1) {
            output.write(buffer, 0, read);
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
        // GNU tar may encode numeric fields in base-256 when values exceed octal field width.
        if ((buffer[offset] & 0x80) != 0) {
            long value = buffer[offset] & 0x7fL;
            for (int i = 1; i < len; i++) {
                value = (value << 8) | (buffer[offset + i] & 0xffL);
            }
            return value;
        }
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
            // Translate CR to LF (no TTY driver to perform ICRNL)
            stdin.write(input.replace("\r\n", "\n").replace("\r", "\n"));
            stdin.flush();
        }

        void interrupt() throws IOException {
            long pid = resolveProcessPid(process);
            Process signal = null;
            try {
                if (pid <= 0) {
                    process.destroy();
                    return;
                }
                String pidText = Long.toString(pid);
                signal = new ProcessBuilder(
                    "/system/bin/sh",
                    "-c",
                    "kill -INT -" + pidText + " >/dev/null 2>&1 || kill -INT " + pidText + " >/dev/null 2>&1"
                ).redirectErrorStream(true).start();
                signal.waitFor(2, TimeUnit.SECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                if (signal != null) signal.destroy();
            }
        }

        private long resolveProcessPid(Process target) {
            try {
                Field pidField = target.getClass().getDeclaredField("pid");
                pidField.setAccessible(true);
                Object value = pidField.get(target);
                if (value instanceof Number) return ((Number) value).longValue();
            } catch (Exception ignored) {
                // Fallback below.
            }
            return -1L;
        }

        ShellSessionRead readDelta(int maxChars) {
            synchronized (outputLock) {
                // If no data yet but process is alive, briefly wait for output
                if (readOffset >= output.length() && process.isAlive()) {
                    try {
                        outputLock.wait(15);
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                    }
                }
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
                outputLock.notifyAll();
            }
        }
    }
}
