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

    private void waitForTrue(String step, String scriptExpression) throws InterruptedException {
        RuntimeException lastRuntime = null;
        AssertionError lastAssertion = null;
        for (int attempt = 1; attempt <= RETRY_COUNT; attempt++) {
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
            Thread.sleep(RETRY_DELAY_MS);
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
