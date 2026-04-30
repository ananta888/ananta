package com.ananta.mobile;

import static androidx.test.espresso.web.assertion.WebViewAssertions.webMatches;
import static androidx.test.espresso.web.model.Atoms.castOrDie;
import static androidx.test.espresso.web.model.Atoms.script;
import static androidx.test.espresso.web.model.Atoms.transform;
import static androidx.test.espresso.web.sugar.Web.onWebView;
import static org.hamcrest.Matchers.is;

import androidx.test.ext.junit.rules.ActivityScenarioRule;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.filters.LargeTest;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.lifecycle.Lifecycle;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
@LargeTest
public class LiveTerminalAndroidE2ETest {
    private static final int RETRY_COUNT = 20;
    private static final long RETRY_DELAY_MS = 1_000L;

    @Rule
    public ActivityScenarioRule<MainActivity> activityRule = new ActivityScenarioRule<>(MainActivity.class);

    private String arg(String key, String fallback) {
        String value = InstrumentationRegistry.getArguments().getString(key);
        return value == null || value.trim().isEmpty() ? fallback : value.trim();
    }

    @Test
    public void liveTerminalShowsOutputForHubAgentView() throws InterruptedException {
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "seed auth and route to agents",
            "localStorage.setItem('ananta.user.token'," + jsLiteral(arg("ananta.e2e.token", "ananta-e2e-token")) + ");"
                + "if(!(window.location.pathname||'').includes('/agents')){window.location.assign('/agents');}"
                + "return true;"
        );

        waitForTrue(
            "agents page loaded",
            "return (window.location.pathname||'').includes('/agents') && document.querySelectorAll('.card').length > 0;"
        );

        waitForTrue(
            "open hub terminal",
            "return (function(){"
                + "var cards=document.querySelectorAll('.card');"
                + "for(var i=0;i<cards.length;i++){"
                + "  var card=cards[i];"
                + "  var strong=card.querySelector('strong');"
                + "  if(!strong){continue;}"
                + "  var name=(strong.textContent||'').trim().toLowerCase();"
                + "  if(name!=='hub'){continue;}"
                + "  var buttons=card.querySelectorAll('button');"
                + "  for(var j=0;j<buttons.length;j++){"
                + "    var label=(buttons[j].textContent||'').toLowerCase();"
                + "    if(label.indexOf('terminal')>=0){buttons[j].click();return true;}"
                + "  }"
                + "}"
                + "return false;"
                + "})();"
        );

        waitForTrue(
            "live terminal heading",
            "return Array.from(document.querySelectorAll('h3')).some(function(el){return (el.textContent||'').indexOf('Live Terminal')>=0;});"
        );
        waitForTrue(
            "terminal output connected marker",
            "var out=document.querySelector(\"[data-testid='terminal-output-buffer']\");"
                + "return !!out && (out.textContent||'').indexOf('connected:')>=0;"
        );
    }

    @Test
    public void ubuntuProotIsUsableFromInstalledApp() throws InterruptedException {
        String smokeCommand =
            "ANANTA_PROOT_RUNTIME=\"proot-runtime\"; "
                + "if [ ! -d \"$ANANTA_PROOT_RUNTIME\" ]; then ANANTA_PROOT_RUNTIME=\"$(pwd)/proot-runtime\"; fi; "
                + "if [ ! -d \"$ANANTA_PROOT_RUNTIME\" ]; then for d in /data/user/0/com.ananta.mobile/files/proot-runtime /data/data/com.ananta.mobile/files/proot-runtime; do "
                + "if [ -d \"$d\" ]; then ANANTA_PROOT_RUNTIME=\"$d\"; break; fi; done; fi; "
                + "[ -n \"$ANANTA_PROOT_RUNTIME\" ] || { echo ANANTA_RUNTIME_MISSING; exit 11; }; "
                + "ANANTA_APK_PATH=\"$(pm path com.ananta.mobile | sed -n '1s/^package://p')\"; "
                + "if [ -n \"$ANANTA_APK_PATH\" ]; then "
                + "ANANTA_LIB_DIR=\"$(dirname \"$ANANTA_APK_PATH\")/lib/arm64\"; "
                + "if [ -f \"$ANANTA_LIB_DIR/libprootclassic.so\" ]; then "
                + "printf '#!/system/bin/sh\\nexec \"%s\" \"$@\"\\n' \"$ANANTA_LIB_DIR/libprootclassic.so\" > \"$ANANTA_PROOT_RUNTIME/bin/proot\"; "
                + "chmod 755 \"$ANANTA_PROOT_RUNTIME/bin/proot\"; "
                + "fi; "
                + "fi; "
                + "ANANTA_ROOTFS=\"$ANANTA_PROOT_RUNTIME/distros/ubuntu/rootfs\"; "
                + "[ -d \"$ANANTA_ROOTFS\" ] || { echo ANANTA_UBUNTU_MISSING; exit 12; }; "
                + "if [ ! -e \"$ANANTA_ROOTFS/bin\" ] && [ ! -e \"$ANANTA_ROOTFS/usr/bin\" ]; then "
                + "for child in \"$ANANTA_ROOTFS\"/*; do "
                + "if [ -d \"$child\" ] && { [ -e \"$child/bin\" ] || [ -e \"$child/usr/bin\" ]; }; then ANANTA_ROOTFS=\"$child\"; break; fi; "
                + "done; "
                + "fi; "
                + "ANANTA_LOGIN_SHELL=\"\"; "
                + "for c in /usr/bin/bash /usr/bin/dash /usr/bin/sh /bin/bash /bin/sh /bin/dash /bin/ash; do "
                + "if [ -f \"$ANANTA_ROOTFS$c\" ]; then ANANTA_LOGIN_SHELL=\"$c\"; break; fi; "
                + "done; "
                + "[ -n \"$ANANTA_LOGIN_SHELL\" ] || { echo ANANTA_LOGIN_SHELL_MISSING; exit 13; }; "
                + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; "
                + "PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" HOME=/root TERM=xterm-256color /system/bin/sh \"$ANANTA_PROOT_RUNTIME/bin/proot\" "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" -w / \"$ANANTA_LOGIN_SHELL\" "
                + "-c 'echo ANANTA_UBUNTU_OK; if command -v python3 >/dev/null 2>&1; then python3 --version; echo ANANTA_PY_OK; elif command -v python >/dev/null 2>&1; then python --version; echo ANANTA_PY_OK; else echo ANANTA_PY_MISSING; fi'";

        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run ubuntu proot smoke via plugin",
            "window.__anantaProotSmoke='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var p=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!p){window.__anantaProotSmoke='ERR:NO_PLUGIN';return;}"
                + "    window.__anantaProotSmoke='CHECK_RUNTIME';"
                + "    var status=await p.getProotRuntimeStatus();"
                + "    var runtimeOk=!!(status && status.prootExecutable);"
                + "    var distros=(status && Array.isArray(status.distros)) ? status.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!runtimeOk){window.__anantaProotSmoke='ERR:RUNTIME_NOT_READY';return;}"
                + "    if(!ubuntuOk){window.__anantaProotSmoke='ERR:UBUNTU_NOT_INSTALLED';return;}"
                + "    window.__anantaProotSmoke='RUN_SMOKE';"
                + "    var res=await p.runShellCommand({command:" + jsLiteral(smokeCommand) + ",timeoutSeconds:120});"
                + "    var out=(res && res.output) ? String(res.output) : '';"
                + "    window.__anantaProotSmoke='OK:' + out;"
                + "  }catch(e){"
                + "    window.__anantaProotSmoke='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "proot smoke finished",
            "var v=String(window.__anantaProotSmoke||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            360,
            1_000L
        );
        runIfPresent(
            "log smoke result",
            "console.log('ANANTA_PROOT_SMOKE_RESULT:' + String(window.__anantaProotSmoke||'')); return true;"
        );
        waitForTrueWithRetry(
            "ubuntu is usable",
            "var v=String(window.__anantaProotSmoke||''); if(v.indexOf('ERR:')===0){ throw new Error(v); } if(v.indexOf('OK:')===0 && v.indexOf('ANANTA_UBUNTU_OK')<0){ throw new Error(v); } return v.indexOf('ANANTA_UBUNTU_OK')>=0;",
            30,
            1_000L
        );
    }

    private void waitForTrue(String step, String scriptExpression) throws InterruptedException {
        waitForTrueWithRetry(step, scriptExpression, RETRY_COUNT, RETRY_DELAY_MS);
    }

    private void waitForTrueWithRetry(String step, String scriptExpression, int retries, long delayMs) throws InterruptedException {
        RuntimeException lastRuntime = null;
        AssertionError lastAssertion = null;
        for (int attempt = 1; attempt <= retries; attempt++) {
            try {
                onWebView().check(
                    webMatches(
                        transform(script(scriptExpression), castOrDie(Boolean.class)),
                        is(true)
                    )
                );
                return;
            } catch (RuntimeException ex) {
                lastRuntime = ex;
            } catch (AssertionError ex) {
                lastAssertion = ex;
            }
            try {
                activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
            } catch (IllegalStateException ignored) {
                // Activity can be recreated/destroyed during long-running setup; retry loop continues.
            }
            Thread.sleep(delayMs);
        }
        AssertionError timeout = new AssertionError("Timed out waiting for: " + step);
        if (lastRuntime != null) timeout.initCause(lastRuntime);
        else if (lastAssertion != null) timeout.initCause(lastAssertion);
        throw timeout;
    }

    private void runIfPresent(String step, String scriptExpression) throws InterruptedException {
        waitForTrue(step, scriptExpression);
    }

    private static String jsLiteral(String value) {
        return "'" + value
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            + "'";
    }
}
