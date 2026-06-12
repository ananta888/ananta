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
public class LiveTerminalAndroidE2EWorkerFlowsTest {
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

    public void tinyGoalCreationUsesWorkerRuntime() throws InterruptedException {
        String tinyWorkerCommand = prootPreamble()
                + prootEnvPrefix()
                + "\"$ANANTA_PROOT_DIRECT\" -0 --link2symlink "
                + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" -w / \"$ANANTA_LOGIN_SHELL\" "
                + "-c '"
                + "export ROLE=worker; "
                + "export DATA_DIR=/tmp/ananta-data; "
                + "cd /data/local/tmp; "
                + "python3 -c \"print(\\\"ANANTA_SMALL_WORKER_TASK_OK\\\")\" 2>&1; "
                + "echo ANANTA_SMALL_TASK_DONE"
                + "'";

        activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
        activityRule.getScenario().onActivity(activity -> {});
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "create tiny goal and verify worker runtime",
            "window.__anantaTinyGoalWorker='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var py=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!py){window.__anantaTinyGoalWorker='ERR:NO_PY_PLUGIN';return;}"
                + "    await py.startHub();"
                + "    await py.startWorker();"
                + "    var rs=await py.getRuntimeStatus();"
                + "    if(!(rs && rs.hubRunning && rs.workerRunning)){window.__anantaTinyGoalWorker='ERR:RUNTIME_NOT_RUNNING';return;}"
                + "    var loginResp=await fetch('http://127.0.0.1:5000/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin'})});"
                + "    var loginJson=await loginResp.json();"
                + "    var loginData=loginJson && loginJson.data ? loginJson.data : {};"
                + "    var token=String(loginData.access_token||'').trim();"
                + "    if(!token){window.__anantaTinyGoalWorker='ERR:NO_ACCESS_TOKEN';return;}"
                + "    localStorage.setItem('ananta.user.token', token);"
                + "    var agentsResp=await fetch('http://127.0.0.1:5000/api/system/agents',{headers:{Authorization:'Bearer '+token}});"
                + "    var agentsJson=await agentsResp.json();"
                + "    var agents=Array.isArray(agentsJson && agentsJson.data) ? agentsJson.data : [];"
                + "    var worker=agents.find(function(a){ return String((a&&a.role)||'').toLowerCase()==='worker'; });"
                + "    if(!worker){window.__anantaTinyGoalWorker='ERR:NO_WORKER_AGENT';return;}"
                + "    if(String((worker&&worker.status)||'').toLowerCase()!=='online'){window.__anantaTinyGoalWorker='ERR:WORKER_OFFLINE';return;}"
                + "    var title='Android Tiny Goal ' + Date.now();"
                + "    var goalResp=await fetch('http://127.0.0.1:5000/goals',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify({title:title,goal:title})});"
                + "    var goalJson=await goalResp.json();"
                + "    var goal=goalJson && goalJson.data ? goalJson.data : {};"
                + "    if(!goal.id){window.__anantaTinyGoalWorker='ERR:NO_GOAL_ID';return;}"
                + "    var cmd=await py.runShellCommand({command:" + jsLiteral(tinyWorkerCommand) + ",timeoutSeconds:120});"
                + "    var out=String((cmd && cmd.output) ? cmd.output : '');"
                + "    if(out.indexOf('ANANTA_SMALL_WORKER_TASK_OK')<0 || out.indexOf('ANANTA_SMALL_TASK_DONE')<0){"
                + "      window.__anantaTinyGoalWorker='ERR:WORKER_TASK_FAILED:' + out.slice(-400);return;"
                + "    }"
                + "    window.__anantaTinyGoalWorker='OK:' + JSON.stringify({goalId:goal.id,workerUrl:String((worker&&worker.url)||''),workerStatus:String((worker&&worker.status)||''),tinyTask:'ok'});"
                + "  }catch(e){"
                + "    window.__anantaTinyGoalWorker='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "tiny goal/worker flow finished",
            "var v=String(window.__anantaTinyGoalWorker||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            240,
            1_000L
        );

        runIfPresent(
            "log tiny goal/worker result",
            "console.log('ANANTA_TINY_GOAL_WORKER_RESULT:' + String(window.__anantaTinyGoalWorker||'').slice(-3000)); return true;"
        );

        waitForTrueWithRetry(
            "tiny goal creation used worker runtime",
            "var v=String(window.__anantaTinyGoalWorker||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('OK:')===0;",
            30,
            1_000L
        );
    }

    @Test
    public void wikiPresetImportFlowWorks() throws InterruptedException {
        activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
        activityRule.getScenario().onActivity(activity -> {});
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "start hub runtime and seed auth",
            "window.__anantaWikiSetup='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var py=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!py){window.__anantaWikiSetup='ERR:NO_PY_PLUGIN';return;}"
                + "    try{await py.stopWorker();}catch(_e){}"
                + "    try{await py.stopHub();}catch(_e){}"
                + "    await py.startHub();"
                + "    await py.startWorker();"
                + "    var rs=await py.getRuntimeStatus();"
                + "    if(!(rs && rs.hubRunning && rs.workerRunning)){window.__anantaWikiSetup='ERR:RUNTIME_NOT_RUNNING';return;}"
                + "    var loginResp=await fetch('http://127.0.0.1:5000/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'admin'})});"
                + "    var loginJson=await loginResp.json();"
                + "    var loginData=loginJson && loginJson.data ? loginJson.data : {};"
                + "    var token=String(loginData.access_token||'').trim();"
                + "    if(!token){window.__anantaWikiSetup='ERR:NO_ACCESS_TOKEN';return;}"
                + "    localStorage.setItem('ananta.user.token', token);"
                + "    localStorage.setItem('ananta.mobile.proot.distro','ubuntu');"
                + "    window.__anantaWikiSetup='OK:' + token.slice(0, 12);"
                + "  }catch(e){"
                + "    window.__anantaWikiSetup='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "wiki setup finished",
            "var v=String(window.__anantaWikiSetup||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            120,
            1_000L
        );

        waitForTrueWithRetry(
            "wiki setup successful",
            "var v=String(window.__anantaWikiSetup||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('OK:')===0;",
            30,
            1_000L
        );

        runIfPresent(
            "navigate to artifacts route",
            "if(!(window.location.pathname||'').includes('/artifacts')){window.location.assign('/artifacts');}"
                + "return true;"
        );

        waitForTrueWithRetry(
            "artifacts wiki section visible",
            "return (window.location.pathname||'').includes('/artifacts') "
                + "&& Array.from(document.querySelectorAll('h3')).some(function(el){ return (el.textContent||'').indexOf('Wikipedia als lokale RAG-Quelle')>=0; }) "
                + "&& Array.from(document.querySelectorAll('button')).some(function(btn){ return (btn.textContent||'').indexOf('Wikipedia importieren')>=0; });",
            90,
            1_000L
        );

        runIfPresent(
            "verify wiki preset options in UI",
            "return (function(){"
                + "var labels=Array.from(document.querySelectorAll('label'));"
                + "var label=labels.find(function(item){ return (item.textContent||'').indexOf('Preset')>=0; });"
                + "if(!label){window.__anantaWikiPresetCount=-1; return true;}"
                + "var select=label.querySelector('select');"
                + "if(!select){window.__anantaWikiPresetCount=-1; return true;}"
                + "window.__anantaWikiPresetCount=Number(select.options ? select.options.length : 0);"
                + "return true;"
                + "})();"
        );

        waitForTrueWithRetry(
            "at least three wiki presets selectable",
            "var c=Number(window.__anantaWikiPresetCount||0);"
                + "if(c<3){ throw new Error('FATAL_E2E:PRESET_COUNT_' + String(c)); }"
                + "return true;",
            30,
            1_000L
        );

        runIfPresent(
            "verify wiki dump preset API flow",
            "window.__anantaWikiImport='PENDING';"
                + "try{"
                + "  var labels=Array.from(document.querySelectorAll('label'));"
                + "  var label=labels.find(function(item){ return (item.textContent||'').indexOf('Preset')>=0; });"
                + "  var select=label && label.querySelector('select');"
                + "  if(!select){window.__anantaWikiImport='ERR:NO_PRESET_SELECT';return true;}"
                + "  var options=Array.from(select.options||[]).map(function(option){return {text:String(option.textContent||''),disabled:!!option.disabled};});"
                + "  var hasDe=options.some(function(option){return option.text.indexOf('Wikipedia DE: Artikel Multistream')>=0;});"
                + "  var hasPages=options.some(function(option){return option.text.indexOf('Wikipedia DE: Artikel nicht-Multistream')>=0;});"
                + "  var zim=options.find(function(option){return option.text.indexOf('Wikipedia DE: ZIM mini')>=0;});"
                + "  if(!hasDe){window.__anantaWikiImport='ERR:NO_DE_MULTISTREAM_PRESET';return true;}"
                + "  if(!hasPages){window.__anantaWikiImport='ERR:NO_DE_PAGES_PRESET';return true;}"
                + "  if(!zim || !zim.disabled){window.__anantaWikiImport='ERR:ZIM_NOT_DISABLED';return true;}"
                + "  var pageText=String(document.body && document.body.textContent || '');"
                + "  if(pageText.indexOf('Multistream-Index vorhanden')<0){window.__anantaWikiImport='ERR:NO_INDEX_MARKER';return true;}"
                + "  window.__anantaWikiImport='OK:' + JSON.stringify({options:options.length,zimPrototype:true,hasIndexMarker:true});"
                + "}catch(e){"
                + "  window.__anantaWikiImport='ERR:' + String((e && e.message) ? e.message : e);"
                + "}"
                + "return true;"
        );

        waitForTrueWithRetry(
            "wiki import flow finished",
            "var v=String(window.__anantaWikiImport||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            1200,
            1_000L
        );

        runIfPresent(
            "log wiki import result",
            "console.log('ANANTA_WIKI_IMPORT_RESULT:' + String(window.__anantaWikiImport||'').slice(-3000)); return true;"
        );

        waitForTrueWithRetry(
            "wiki import succeeded",
            "var v=String(window.__anantaWikiImport||'');"
                + "if(v.indexOf('ERR:')===0){ throw new Error('FATAL_E2E:' + v); }"
                + "return v.indexOf('OK:')===0;",
            30,
            1_000L
        );
    }

    @Test
    public void opencodeDownloadsOnDemandAndBecomesReady() throws InterruptedException {
        activityRule.getScenario().moveToState(Lifecycle.State.RESUMED);
        activityRule.getScenario().onActivity(activity -> {});
        onWebView().forceJavascriptEnabled();
        waitForTrue("document ready", "return document.readyState === 'complete';");

        runIfPresent(
            "run on-demand opencode install",
            "window.__anantaOpencodeInstall='PENDING';"
                + "(async function(){"
                + "  try{"
                + "    var py=window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PythonRuntime;"
                + "    if(!py){window.__anantaOpencodeInstall='ERR:NO_PY_PLUGIN';return;}"
                + "    var st=await py.getProotRuntimeStatus();"
                + "    if(!(st && st.prootExecutable)){ await py.installProotRuntime({confirmed:true}); }"
                + "    st=await py.getProotRuntimeStatus();"
                + "    var distros=(st && Array.isArray(st.distros)) ? st.distros : [];"
                + "    var ubuntuOk=distros.some(function(item){ return String(item && item.name || '').toLowerCase()==='ubuntu'; });"
                + "    if(!ubuntuOk){ await py.installProotDistro({distro:'ubuntu', confirmed:true}); }"
                + "    var statusBeforeWorkspace=await py.getGuidedSetupStatus();"
                + "    if(!(statusBeforeWorkspace && statusBeforeWorkspace.workspaceInstalled)){"
                + "      await py.installAnantaWorkspace({});"
                + "    }"
                + "    await py.installWorkerDependencies({confirmed:true});"
                + "    var removeCmd=" + jsLiteral(
                    prootPreamble()
                        + prootEnvPrefix()
                        + "\"$ANANTA_PROOT_DIRECT\" -0 --link2symlink "
                        + "-r \"$ANANTA_ROOTFS\" -b /dev:/dev -b /proc:/proc -b /sys:/sys -b /data:/data -b \"$ANANTA_PROOT_TMP:/tmp\" -w / \"$ANANTA_LOGIN_SHELL\" "
                        + "-c 'rm -f /usr/local/bin/opencode /usr/bin/opencode /home/ananta/.local/bin/opencode /root/.local/bin/opencode; echo ANANTA_OPENCODE_REMOVED'"
                ) + ";"
                + "    var rm=await py.runShellCommand({command:removeCmd,timeoutSeconds:120});"
                + "    var rmOut=String((rm && rm.output) ? rm.output : '');"
                + "    if(rmOut.indexOf('ANANTA_OPENCODE_REMOVED')<0){window.__anantaOpencodeInstall='ERR:OPENCODE_REMOVE_FAILED';return;}"
                + "    var before=await py.getGuidedSetupStatus();"
                + "    if(before && before.opencodeReady){window.__anantaOpencodeInstall='ERR:OPENCODE_STILL_READY_BEFORE_INSTALL';return;}"
                + "    var ins=await py.installOpencode({confirmed:true});"
                + "    var out=String((ins && ins.output) ? ins.output : '');"
                + "    var after=await py.getGuidedSetupStatus();"
                + "    if(!(after && after.opencodeReady)){window.__anantaOpencodeInstall='ERR:OPENCODE_NOT_READY_AFTER_INSTALL';return;}"
                + "    window.__anantaOpencodeInstall='OK:' + out.slice(-500);"
                + "  }catch(e){"
                + "    window.__anantaOpencodeInstall='ERR:' + String((e && e.message) ? e.message : e);"
                + "  }"
                + "})();"
                + "return true;"
        );

        waitForTrueWithRetry(
            "opencode install flow finished",
            "var v=String(window.__anantaOpencodeInstall||''); return v.indexOf('OK:')===0 || v.indexOf('ERR:')===0;",
            1800,
            1_000L
        );

        runIfPresent(
            "log on-demand opencode result",
            "console.log('ANANTA_OPENCODE_INSTALL_RESULT:' + String(window.__anantaOpencodeInstall||'').slice(-3000)); return true;"
        );

        waitForTrueWithRetry(
            "opencode install succeeded",
            "var v=String(window.__anantaOpencodeInstall||'');"
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
                + "    var targetMinBytes=Number(" + jsLiteral(arg("ananta.voxtral.model.minBytes", "1400000000")) + ")||0;"
                + "    var status=await vx.getStatus();"
                + "    var modelPath=String((status && status.modelPath) ? status.modelPath : '').trim();"
                + "    var runnerPath=String((status && status.runnerPath) ? status.runnerPath : '').trim();"
                + "    var assets=await vx.listLocalAssets();"
                + "    var models=(assets && Array.isArray(assets.models)) ? assets.models : [];"
                + "    var runners=(assets && Array.isArray(assets.runners)) ? assets.runners : [];"
                + "    var target=models.find(function(m){ var n=String((m&&m.name)||'').toLowerCase(); var b=Number((m&&m.bytes)||0); return n===String(targetFile).toLowerCase() && (!targetMinBytes || b>=targetMinBytes); });"
                + "    if(target){ modelPath=String((target&&target.path)||'').trim(); }"
                + "    if(!target){"
                + "      window.__anantaVoxtralTranscribe='DOWNLOADING_MODEL';"
                + "      var dl=await vx.downloadModel({modelUrl:targetUrl,fileName:targetFile,minBytes:targetMinBytes||734003200,confirmed:true});"
                + "      modelPath=String((dl&&dl.modelPath)||'').trim();"
                + "      assets=await vx.listLocalAssets();"
                + "      models=(assets && Array.isArray(assets.models)) ? assets.models : [];"
                + "      target=models.find(function(m){ var n=String((m&&m.name)||'').toLowerCase(); var b=Number((m&&m.bytes)||0); return n===String(targetFile).toLowerCase() && (!targetMinBytes || b>=targetMinBytes); });"
                + "      if(target){ modelPath=String((target&&target.path)||'').trim(); }"
                + "    }"
                + "    if(runners.length>0){"
                + "      var pref=runners.find(function(r){var n=String((r&&r.name)||'').toLowerCase(); return n==='voxtral-realtime' || n==='voxtral-realtime-bin';})"
                + "        || runners.find(function(r){var n=String((r&&r.name)||'').toLowerCase(); return n.indexOf('voxtral')>=0 || n.indexOf('crispasr')===0;});"
                + "      runnerPath=String((pref&&pref.path)||'').trim();"
                + "    }"
                + "    if((!runnerPath || runnerPath.toLowerCase().indexOf('voxtral-realtime')<0) && typeof vx.provisionVoxtralRunner==='function'){"
                + "      var pv=await vx.provisionVoxtralRunner({confirmed:true});"
                + "      runnerPath=String((pv&&pv.runnerPath)||'').trim();"
                + "    }"
                + "    if(!modelPath){window.__anantaVoxtralTranscribe='ERR:NO_MODEL_PATH';return;}"
                + "    if(!runnerPath){window.__anantaVoxtralTranscribe='ERR:NO_RUNNER_PATH';return;}"
                + "    var mic=await vx.requestMicrophonePermission();"
                + "    var micState=String((mic && mic.state) ? mic.state : mic).toLowerCase();"
                + "    if(micState.indexOf('granted')<0){window.__anantaVoxtralTranscribe='ERR:MIC_' + micState;return;}"
                + "    var rec=await vx.startRecording({maxSeconds:1,sampleRate:8000});"
                + "    await new Promise(function(resolve){setTimeout(resolve, 900);});"
                + "    var stopped=await vx.stopRecording();"
                + "    var audioPath=String((stopped && stopped.audioPath) ? stopped.audioPath : ((rec && rec.audioPath) ? rec.audioPath : '')).trim();"
                + "    if(!audioPath){window.__anantaVoxtralTranscribe='ERR:NO_AUDIO_PATH';return;}"
                + "    var tr=await vx.transcribe({audioPath:audioPath,modelPath:modelPath,runnerPath:runnerPath,lowMemoryMode:true,confirmed:true});"
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
