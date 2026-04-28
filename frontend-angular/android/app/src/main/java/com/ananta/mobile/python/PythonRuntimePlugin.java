package com.ananta.mobile.python;

import android.content.Context;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

@CapacitorPlugin(name = "PythonRuntime")
public class PythonRuntimePlugin extends Plugin {
    private static final int DEFAULT_SHELL_TIMEOUT_SECONDS = 20;
    private static final int MAX_SHELL_TIMEOUT_SECONDS = 600;
    private static final int MAX_SHELL_OUTPUT_CHARS = 120_000;
    private static final int MAX_SESSION_OUTPUT_CHARS = 200_000;

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
        env.putIfAbsent("TERM", "xterm-256color");
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
