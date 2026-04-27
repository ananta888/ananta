package com.ananta.mobile.python;

import android.content.Context;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@CapacitorPlugin(name = "PythonRuntime")
public class PythonRuntimePlugin extends Plugin {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
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

    @Override
    protected void handleOnDestroy() {
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
        Method start = pythonClass.getMethod("start", androidPlatformClass);
        start.invoke(null, androidPlatform);
    }
}
