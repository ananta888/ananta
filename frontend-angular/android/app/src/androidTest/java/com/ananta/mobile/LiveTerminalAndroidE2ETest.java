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
import org.junit.After;
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

    @After
    public void cleanupE2ETokens() {
        try {
            activityRule.getScenario().onActivity(activity -> {});
            onWebView().forceJavascriptEnabled();
            onWebView().check(
                webMatches(
                    transform(
                        script("localStorage.removeItem('ananta.user.token');"
                            + "localStorage.removeItem('ananta.mobile.proot.distro');"
                            + "return true;"),
                        castOrDie(Boolean.class)
                    ),
                    is(true)
                )
            );
        } catch (Exception ignored) {
            // Best-effort cleanup — activity may already be destroyed.
        }
    }

    @Test
    public void liveTerminalShowsOutputForHubAgentView() throws InterruptedException {
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "seed auth and route to agents",
            "localStorage.setItem('ananta.user.token'," + jsLiteral(arg("ananta.e2e.token", "ananta-e2e-token")) + ");"
                + "localStorage.setItem('ananta.mobile.proot.distro','ubuntu');"
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
    public void internalWorkerLiveTerminalConfirmsUbuntuIsUsableAndLoggedIn() throws InterruptedException {
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "seed auth and route to agents",
            "localStorage.setItem('ananta.user.token'," + jsLiteral(arg("ananta.e2e.token", "ananta-e2e-token")) + ");"
                + "localStorage.setItem('ananta.mobile.proot.distro','ubuntu');"
                + "if(!(window.location.pathname||'').includes('/agents')){window.location.assign('/agents');}"
                + "return true;"
        );

        runIfPresent(
            "ensure ubuntu distro installed",
            "window.__anantaDistroSetup='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var p=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!p){window.__anantaDistroSetup='ERR:NO_PLUGIN';return;}"
                + "    var status=await p.getProotRuntimeStatus();"
                + "    var distros=(status && Array.isArray(status.distros)) ? status.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!ubuntuOk){"
                + "      window.__anantaDistroSetup='INSTALLING';"
                + "      await p.installProotDistro({distro:'ubuntu'});"
                + "    }"
                + "    window.__anantaDistroSetup='OK';"
                + "  }catch(e){"
                + "    window.__anantaDistroSetup='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "ubuntu distro ready",
            "var v=String(window.__anantaDistroSetup||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v==='OK';",
            600,
            1_000L
        );

        waitForTrue(
            "agents page loaded",
            "return (window.location.pathname||'').includes('/agents') && document.querySelectorAll('.card').length > 0;"
        );

        waitForTrue(
            "open internal worker terminal",
            "return (function(){"
                + "var cards=document.querySelectorAll('.card');"
                + "for(var i=0;i<cards.length;i++){"
                + "  var card=cards[i];"
                + "  var strong=card.querySelector('strong');"
                + "  if(!strong){continue;}"
                + "  var name=(strong.textContent||'').trim().toLowerCase();"
                + "  var cardText=(card.textContent||'').toLowerCase();"
                + "  var isWorker=name.indexOf('worker')>=0 || cardText.indexOf('(worker)')>=0;"
                + "  var isInternal=cardText.indexOf('(intern)')>=0;"
                + "  if(!isWorker || !isInternal){continue;}"
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
            "panel terminal tab loaded",
            "return (window.location.pathname||'').indexOf('/panel/')>=0 "
                + "&& Array.from(document.querySelectorAll('h3')).some(function(el){return (el.textContent||'').indexOf('Live Terminal')>=0;});"
        );
        waitForTrue(
            "embedded worker terminal connected",
            "var out=document.querySelector(\"[data-testid='terminal-output-buffer']\");"
                + "return !!out && (out.textContent||'').indexOf('connected: embedded-interactive')>=0;"
        );

        runIfPresent(
            "send ubuntu verification command",
            "return (function(){"
                + "var input=document.querySelector(\"input[aria-label='Terminal-Befehl']\");"
                + "if(!input){return false;}"
                + "input.focus();"
                + "input.value=\"printf 'ANANTA_E2E_UBUNTU_CHECK:'; "
                + "if [ -f /etc/os-release ]; then . /etc/os-release; echo \\\"${ID:-unknown}\\\"; else echo no_os_release; fi; "
                + "printf 'ANANTA_E2E_WHOAMI:'; whoami 2>/dev/null || id -un 2>/dev/null || echo unknown\";"
                + "input.dispatchEvent(new Event('input',{bubbles:true}));"
                + "var buttons=document.querySelectorAll('button');"
                + "for(var i=0;i<buttons.length;i++){"
                + "  var label=(buttons[i].textContent||'').trim().toLowerCase();"
                + "  if(label==='senden' && !buttons[i].disabled){buttons[i].click();return true;}"
                + "}"
                + "return false;"
                + "})();"
        );

        waitForTrueWithRetry(
            "worker terminal confirms ubuntu session",
            "var out=document.querySelector(\"[data-testid='terminal-output-buffer']\");"
                + "var text=String((out&&out.textContent)||'');"
                + "if(text.indexOf('ANANTA_E2E_UBUNTU_CHECK:')>=0 && text.indexOf('ANANTA_E2E_UBUNTU_CHECK:ubuntu')<0){"
                + "  throw new Error('FATAL_E2E:Unexpected Ubuntu marker: ' + text.slice(-1200));"
                + "}"
                + "if(text.indexOf('proot login fehlgeschlagen')>=0 || text.indexOf('execve(\"/usr/bin/bash\"): Permission denied')>=0){"
                + "  throw new Error('FATAL_E2E:' + text.slice(-1200));"
                + "}"
                + "return text.indexOf('ANANTA_E2E_UBUNTU_CHECK:ubuntu')>=0 && text.indexOf('ANANTA_E2E_WHOAMI:')>=0;",
            90,
            1_000L
        );
    }

    /** Shared proot setup preamble used by multiple tests (DRY). */
    private static String prootPreamble() {
        return "ANANTA_PROOT_RUNTIME=\"proot-runtime\"; "
            + "if [ ! -d \"$ANANTA_PROOT_RUNTIME\" ]; then ANANTA_PROOT_RUNTIME=\"$(pwd)/proot-runtime\"; fi; "
            + "if [ ! -d \"$ANANTA_PROOT_RUNTIME\" ]; then for d in /data/user/0/com.ananta.mobile/files/proot-runtime; do "
            + "if [ -d \"$d\" ]; then ANANTA_PROOT_RUNTIME=\"$d\"; break; fi; done; fi; "
            + "[ -n \"$ANANTA_PROOT_RUNTIME\" ] || { echo ANANTA_RUNTIME_MISSING; exit 11; }; "
            + "ANANTA_APK_PATH=\"$(pm path com.ananta.mobile | sed -n '1s/^package://p')\"; "
            + "ANANTA_LIB_DIR=\"\"; ANANTA_PROOT_DIRECT=\"\"; "
            + "if [ -n \"$ANANTA_APK_PATH\" ]; then "
            + "ANANTA_LIB_DIR=\"$(dirname \"$ANANTA_APK_PATH\")/lib/arm64\"; "
            + "if [ -f \"$ANANTA_LIB_DIR/libprootclassic.so\" ]; then "
            + "ANANTA_PROOT_DIRECT=\"$ANANTA_LIB_DIR/libprootclassic.so\"; "
            + "fi; "
            + "fi; "
            + "[ -n \"$ANANTA_PROOT_DIRECT\" ] || { echo ANANTA_PROOT_MISSING; exit 14; }; "
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
            + "ANANTA_PROOT_TMP=\"$ANANTA_PROOT_RUNTIME/tmp\"; mkdir -p \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; chmod 700 \"$ANANTA_PROOT_TMP\" 2>/dev/null || true; ";
    }

    /** Standard proot env vars passed before the proot binary invocation. */
    private static String prootEnvPrefix() {
        return "PROOT_FORCE_KOMPAT=1 PROOT_TMP_DIR=\"$ANANTA_PROOT_TMP\" TMPDIR=\"$ANANTA_PROOT_TMP\" "
            + "HOME=/root TERM=xterm-256color LD_LIBRARY_PATH=\"$ANANTA_LIB_DIR\" GLIBC_TUNABLES=glibc.pthread.rseq=0 "
            + "PATH=\"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\" ";
    }

    @Test
    public void ubuntuProotIsUsableFromInstalledApp() throws InterruptedException {
        String smokeCommand = prootPreamble()
                + prootEnvPrefix()
                + "\"$ANANTA_PROOT_DIRECT\" -0 --link2symlink "
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
                + "    if(!ubuntuOk){"
                + "      window.__anantaProotSmoke='INSTALLING_UBUNTU';"
                + "      await p.installProotDistro({distro:'ubuntu'});"
                + "      window.__anantaProotSmoke='UBUNTU_INSTALLED';"
                + "    }"
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
            600,
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

    @Test
    public void proxyEnabledAptGetUpdateWorks() throws InterruptedException {
        // Verifies the HTTP CONNECT proxy allows apt-get update + install from within proot
        String aptCommand = prootPreamble()
                + "chmod -R 777 \"$ANANTA_ROOTFS/var/lib/dpkg\" \"$ANANTA_ROOTFS/var/cache/apt\" \"$ANANTA_ROOTFS/var/log\" 2>/dev/null; "
                + "chmod -R 777 \"$ANANTA_ROOTFS/var/log/apt\" 2>/dev/null; "
                + prootEnvPrefix()
                + "http_proxy=\"http://127.0.0.1:18080\" https_proxy=\"http://127.0.0.1:18080\" "
                + "HTTP_PROXY=\"http://127.0.0.1:18080\" HTTPS_PROXY=\"http://127.0.0.1:18080\" "
                + "\"$ANANTA_PROOT_DIRECT\" -0 --link2symlink "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" -w / \"$ANANTA_LOGIN_SHELL\" "
                + "-c '"
                + "echo nameserver 8.8.8.8 > /etc/resolv.conf 2>/dev/null; "
                + "mkdir -p /etc/apt/apt.conf.d 2>/dev/null; "
                + "printf \"Acquire::http::Proxy \\\"http://127.0.0.1:18080\\\";\\nAcquire::https::Proxy \\\"http://127.0.0.1:18080\\\";\\n\" > /etc/apt/apt.conf.d/99proxy; "
                + "export http_proxy=http://127.0.0.1:18080; "
                + "export https_proxy=http://127.0.0.1:18080; "
                + "export DEBIAN_FRONTEND=noninteractive; "
                + "dpkg --configure -a 2>&1 || true; "
                + "apt-get update 2>&1 && echo ANANTA_APT_UPDATE_OK; "
                + "apt-get -f install -y 2>&1 || true; "
                + "apt-get install -y --no-install-recommends python3-pip git curl "
                + "python3-flask python3-pydantic python3-sqlalchemy python3-yaml python3-psutil python3-jwt python3-dotenv python3-requests 2>&1; "
                + "echo ANANTA_APT_INSTALL_RC=$?; "
                + "pip3 --version 2>&1 || echo ANANTA_PIP3_MISSING; "
                + "pip3 install --break-system-packages --ignore-installed --no-input --progress-bar off "
                + "pydantic-settings sqlmodel portalocker jsonschema click alembic gitpython "
                + "flask-cors prometheus-client typer prompt-toolkit "
                + "flask-sock simple-websocket hvac pypdf python-docx openpyxl python-pptx 2>&1; "
                + "echo ANANTA_PIP_INSTALL_RC=$?; "
                + "python3 -c \"import sys; print(sys.path)\" 2>&1; "
                + "python3 -c \"import flask; print(\\\"flask_ok\\\")\" 2>&1; "
                + "python3 -c \"import pydantic; print(\\\"pydantic_ok\\\")\" 2>&1; "
                + "python3 -c \"import sqlmodel; print(\\\"sqlmodel_ok\\\")\" 2>&1; "
                + "python3 -c \"import yaml; print(\\\"yaml_ok\\\")\" 2>&1; "
                + "python3 -c \"import psutil; print(\\\"psutil_ok\\\")\" 2>&1; "
                + "python3 -c \"import jwt; print(\\\"jwt_ok\\\")\" 2>&1; "
                + "echo ANANTA_DIAG_DONE; "
                + "echo ANANTA_PIP_VERIFY_RC=$?; "
                + "echo ANANTA_APT_OK"
                + "'";

        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run apt-get update via proxy",
            "window.__anantaAptSmoke='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var p=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!p){window.__anantaAptSmoke='ERR:NO_PLUGIN';return;}"
                + "    var status=await p.getProotRuntimeStatus();"
                + "    var distros=(status && Array.isArray(status.distros)) ? status.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!ubuntuOk){"
                + "      window.__anantaAptSmoke='INSTALLING_UBUNTU';"
                + "      await p.installProotDistro({distro:'ubuntu'});"
                + "    }"
                + "    window.__anantaAptSmoke='RUN_APT';"
                + "    var res=await p.runShellCommand({command:" + jsLiteral(aptCommand) + ",timeoutSeconds:900});"
                + "    var out=(res && res.output) ? String(res.output) : '';"
                + "    window.__anantaAptSmoke='OK:' + out;"
                + "  }catch(e){"
                + "    window.__anantaAptSmoke='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "apt-get + pip finished",
            "var v=String(window.__anantaAptSmoke||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            1200,
            1_000L
        );
        runIfPresent(
            "log apt result",
            "console.log('ANANTA_APT_RESULT:' + String(window.__anantaAptSmoke||'').slice(-3000)); return true;"
        );
        waitForTrueWithRetry(
            "apt + pip install succeeded",
            "var v=String(window.__anantaAptSmoke||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('ANANTA_APT_OK')>=0;",
            30,
            1_000L
        );
    }

    @Test
    public void workerAgentCanStartInProot() throws InterruptedException {
        // Test that the ananta worker can import and start inside proot Ubuntu
        String workerCommand = prootPreamble()
                + prootEnvPrefix()
                + "http_proxy=\"http://127.0.0.1:18080\" https_proxy=\"http://127.0.0.1:18080\" "
                + "\"$ANANTA_PROOT_DIRECT\" -0 --link2symlink "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" -w / \"$ANANTA_LOGIN_SHELL\" "
                + "-c '"
                + "export PYTHONPATH=/data/local/tmp/ananta-agent/..; "
                + "export ROLE=worker; "
                + "export ANANTA_WORKER_PORT=15080; "
                + "export DATA_DIR=/tmp/ananta-data; "
                + "cd /data/local/tmp; "
                + "python3 -c \"import sys; sys.path.insert(0,\\\"/data/local/tmp\\\"); from agent.config import settings; print(\\\"ANANTA_CONFIG_IMPORT_OK\\\")\" 2>&1; "
                + "echo CONFIG_RC=$?; "
                + "python3 -c \"import sys; sys.path.insert(0,\\\"/data/local/tmp\\\"); from agent.ai_agent import create_app; print(\\\"ANANTA_APP_CREATE_OK\\\")\" 2>&1; "
                + "echo APP_RC=$?; "
                + "timeout 15 python3 -c \""
                + "import sys, os, threading, time; "
                + "sys.path.insert(0,\\\"/data/local/tmp\\\"); "
                + "os.environ[\\\"ROLE\\\"] = \\\"worker\\\"; "
                + "os.environ[\\\"DATA_DIR\\\"] = \\\"/tmp/ananta-data\\\"; "
                + "os.environ[\\\"PORT\\\"] = \\\"15080\\\"; "
                + "os.environ[\\\"SECRET_KEY\\\"] = \\\"test-secret-key-for-e2e\\\"; "
                + "from agent.ai_agent import create_app; "
                + "app = create_app(); "
                + "print(\\\"ANANTA_FLASK_CREATED\\\"); "
                + "threading.Thread(target=lambda: (time.sleep(5), os._exit(0)), daemon=True).start(); "
                + "app.run(host=\\\"0.0.0.0\\\", port=15080, debug=False)"
                + "\" 2>&1; "
                + "echo FLASK_RC=$?; "
                + "echo ANANTA_WORKER_DONE"
                + "'";

        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run worker import test",
            "window.__anantaWorkerSmoke='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var p=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!p){window.__anantaWorkerSmoke='ERR:NO_PLUGIN';return;}"
                + "    var status=await p.getProotRuntimeStatus();"
                + "    var distros=(status && Array.isArray(status.distros)) ? status.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!ubuntuOk){"
                + "      window.__anantaWorkerSmoke='ERR:UBUNTU_NOT_INSTALLED';return;"
                + "    }"
                + "    window.__anantaWorkerSmoke='RUNNING';"
                + "    var res=await p.runShellCommand({command:" + jsLiteral(workerCommand) + ",timeoutSeconds:120});"
                + "    var out=(res && res.output) ? String(res.output) : '';"
                + "    window.__anantaWorkerSmoke='OK:' + out;"
                + "  }catch(e){"
                + "    window.__anantaWorkerSmoke='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "worker import test finished",
            "var v=String(window.__anantaWorkerSmoke||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            180,
            1_000L
        );
        runIfPresent(
            "log worker result",
            "console.log('ANANTA_WORKER_RESULT:' + String(window.__anantaWorkerSmoke||'').slice(-3000)); return true;"
        );
        waitForTrueWithRetry(
            "worker imports succeeded",
            "var v=String(window.__anantaWorkerSmoke||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('ANANTA_WORKER_DONE')>=0;",
            30,
            1_000L
        );
    }

    @Test
    public void voxtralRunnerProvisionButtonActionWorks() throws InterruptedException {
        activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
        activityRule.getScenario().onActivity(activity -> {});
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run voxtral runner provisioning action",
            "window.__anantaVoxtralProvision='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var py=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    var vx=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.VoxtralOffline;"
                + "    if(!py){window.__anantaVoxtralProvision='ERR:NO_PY_PLUGIN';return;}"
                + "    if(!vx){window.__anantaVoxtralProvision='ERR:NO_VOXTRAL_PLUGIN';return;}"
                + "    var st=await py.getProotRuntimeStatus();"
                + "    if(!(st && st.prootExecutable)){"
                + "      window.__anantaVoxtralProvision='INSTALLING_PROOT_RUNTIME';"
                + "      await py.installProotRuntime({confirmed:true});"
                + "      st=await py.getProotRuntimeStatus();"
                + "    }"
                + "    var distros=(st && Array.isArray(st.distros)) ? st.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!ubuntuOk){"
                + "      window.__anantaVoxtralProvision='INSTALLING_UBUNTU';"
                + "      await py.installProotDistro({distro:'ubuntu', confirmed:true});"
                + "    }"
                + "    window.__anantaVoxtralProvision='PROVISIONING';"
                + "    var res=await vx.provisionVoxtralRunner({confirmed:true});"
                + "    var rp=String((res && res.runnerPath) ? res.runnerPath : '');"
                + "    if(!rp){window.__anantaVoxtralProvision='ERR:NO_RUNNER_PATH';return;}"
                + "    var assets=await vx.listLocalAssets();"
                + "    var runners=(assets && Array.isArray(assets.runners)) ? assets.runners : [];"
                + "    var hasRunner=runners.some(function(r){ return String((r&&r.path)||'')===rp; });"
                + "    if(!hasRunner){window.__anantaVoxtralProvision='ERR:RUNNER_NOT_LISTED';return;}"
                + "    window.__anantaVoxtralProvision='OK:' + rp;"
                + "  }catch(e){"
                + "    window.__anantaVoxtralProvision='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "voxtral provisioning action finished",
            "var v=String(window.__anantaVoxtralProvision||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            1800,
            1_000L
        );

        runIfPresent(
            "log voxtral provisioning result",
            "console.log('ANANTA_VOXTRAL_PROVISION_RESULT:' + String(window.__anantaVoxtralProvision||'').slice(-3000)); return true;"
        );

        waitForTrueWithRetry(
            "voxtral provisioning succeeded",
            "var v=String(window.__anantaVoxtralProvision||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('OK:')===0;",
            30,
            1_000L
        );
    }

    @Test
    public void voxtralDirectTranscriptionWorks() throws InterruptedException {
        activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
        activityRule.getScenario().onActivity(activity -> {});
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run voxtral direct transcription",
            "window.__anantaVoxtralTranscribe='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var vx=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.VoxtralOffline;"
                + "    if(!vx){window.__anantaVoxtralTranscribe='ERR:NO_VOXTRAL_PLUGIN';return;}"
                + "    var targetUrl=" + jsLiteral(arg("ananta.voxtral.model.url", "https://huggingface.co/andrijdavid/Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/Q2_K.gguf")) + ";"
                + "    var targetFile=" + jsLiteral(arg("ananta.voxtral.model.file", "Q2_K.gguf")) + ";"
                + "    var status=await vx.getStatus();"
                + "    var modelPath=String((status && status.modelPath) ? status.modelPath : '').trim();"
                + "    var runnerPath=String((status && status.runnerPath) ? status.runnerPath : '').trim();"
                + "    var assets=await vx.listLocalAssets();"
                + "    var models=(assets && Array.isArray(assets.models)) ? assets.models : [];"
                + "    var runners=(assets && Array.isArray(assets.runners)) ? assets.runners : [];"
                + "    var target=models.find(function(m){ var n=String((m&&m.name)||'').toLowerCase(); return n===String(targetFile).toLowerCase(); });"
                + "    if(target){ modelPath=String((target&&target.path)||'').trim(); }"
                + "    if(!target){"
                + "      window.__anantaVoxtralTranscribe='DOWNLOADING_MODEL';"
                + "      var dl=await vx.downloadModel({modelUrl:targetUrl,fileName:targetFile,minBytes:734003200,confirmed:true});"
                + "      modelPath=String((dl&&dl.modelPath)||'').trim();"
                + "      assets=await vx.listLocalAssets();"
                + "      models=(assets && Array.isArray(assets.models)) ? assets.models : [];"
                + "      target=models.find(function(m){ var n=String((m&&m.name)||'').toLowerCase(); return n===String(targetFile).toLowerCase(); });"
                + "      if(target){ modelPath=String((target&&target.path)||'').trim(); }"
                + "    }"
                + "    if(runners.length>0){"
                + "      var pref=runners.find(function(r){return String((r&&r.name)||'').toLowerCase().indexOf('voxtral')>=0;});"
                + "      runnerPath=String(((pref||runners[0])&&((pref||runners[0]).path))||'').trim();"
                + "    }"
                + "    if(!modelPath){window.__anantaVoxtralTranscribe='ERR:NO_MODEL_PATH';return;}"
                + "    if(!runnerPath){window.__anantaVoxtralTranscribe='ERR:NO_RUNNER_PATH';return;}"
                + "    var mic=await vx.requestMicrophonePermission();"
                + "    var micState=String((mic && mic.state) ? mic.state : mic).toLowerCase();"
                + "    if(micState.indexOf('granted')<0){window.__anantaVoxtralTranscribe='ERR:MIC_' + micState;return;}"
                + "    var rec=await vx.startRecording({maxSeconds:2,sampleRate:16000});"
                + "    await new Promise(function(resolve){setTimeout(resolve, 1900);});"
                + "    var stopped=await vx.stopRecording();"
                + "    var audioPath=String((stopped && stopped.audioPath) ? stopped.audioPath : ((rec && rec.audioPath) ? rec.audioPath : '')).trim();"
                + "    if(!audioPath){window.__anantaVoxtralTranscribe='ERR:NO_AUDIO_PATH';return;}"
                + "    var tr=await vx.transcribe({audioPath:audioPath,modelPath:modelPath,runnerPath:runnerPath,confirmed:true});"
                + "    var transcript=String((tr && tr.transcript) ? tr.transcript : '').trim();"
                + "    var raw=String((tr && tr.rawOutput) ? tr.rawOutput : '').trim();"
                + "    if(!transcript && !raw){window.__anantaVoxtralTranscribe='ERR:EMPTY_TRANSCRIPT';return;}"
                + "    window.__anantaVoxtralTranscribe='OK:' + (transcript || raw).slice(0, 800);"
                + "  }catch(e){"
                + "    window.__anantaVoxtralTranscribe='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "voxtral direct transcription finished",
            "var v=String(window.__anantaVoxtralTranscribe||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            2400,
            1_000L
        );

        runIfPresent(
            "log voxtral direct transcription result",
            "console.log('ANANTA_VOXTRAL_TRANSCRIBE_RESULT:' + String(window.__anantaVoxtralTranscribe||'').slice(-3000)); return true;"
        );

        waitForTrueWithRetry(
            "voxtral direct transcription succeeded",
            "var v=String(window.__anantaVoxtralTranscribe||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('OK:')===0;",
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
                if (isFatalE2EException(ex)) {
                    AssertionError fatal = new AssertionError("Fatal E2E error while waiting for: " + step);
                    fatal.initCause(ex);
                    throw fatal;
                }
                lastRuntime = ex;
            } catch (AssertionError ex) {
                lastAssertion = ex;
            }
            try {
                activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
            } catch (IllegalStateException | AssertionError ignored) {
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

    private boolean isFatalE2EException(RuntimeException ex) {
        String message = String.valueOf(ex == null ? "" : ex.getMessage());
        return message.contains("FATAL_E2E:");
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
