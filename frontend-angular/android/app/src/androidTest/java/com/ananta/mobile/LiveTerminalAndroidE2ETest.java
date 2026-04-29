package com.ananta.mobile;

import static androidx.test.espresso.web.assertion.WebViewAssertions.webMatches;
import static androidx.test.espresso.web.model.Atoms.getText;
import static androidx.test.espresso.web.model.Atoms.webClick;
import static androidx.test.espresso.web.model.Atoms.webKeys;
import static androidx.test.espresso.web.model.DriverAtoms.findElement;
import static androidx.test.espresso.web.sugar.Web.onWebView;
import static org.hamcrest.Matchers.containsString;

import androidx.test.ext.junit.rules.ActivityScenarioRule;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.filters.LargeTest;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.espresso.web.webdriver.Locator;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
@LargeTest
public class LiveTerminalAndroidE2ETest {

    @Rule
    public ActivityScenarioRule<MainActivity> activityRule = new ActivityScenarioRule<>(MainActivity.class);

    private String arg(String key, String fallback) {
        String value = InstrumentationRegistry.getArguments().getString(key);
        return value == null || value.trim().isEmpty() ? fallback : value.trim();
    }

    @Test
    public void liveTerminalShowsOutputForHubAgentView() throws InterruptedException {
        onWebView().forceJavascriptEnabled();

        onWebView().withElement(findElement(Locator.ID, "username"))
            .perform(webKeys(arg("ananta.e2e.username", "admin")));
        onWebView().withElement(findElement(Locator.ID, "password"))
            .perform(webKeys(arg("ananta.e2e.password", "AnantaAdminPassword123!")));
        onWebView().withElement(findElement(Locator.CSS_SELECTOR, "button[type='submit']"))
            .perform(webClick());

        Thread.sleep(1_500);

        onWebView().withElement(findElement(Locator.XPATH, "//a[@href='/agents']"))
            .perform(webClick());
        onWebView().withElement(findElement(Locator.XPATH, "(//strong[translate(normalize-space(text()), 'HUB', 'hub')='hub']/ancestor::div[contains(@class,'card')]//button[contains(.,'Terminal')])[1]"))
            .perform(webClick());

        Thread.sleep(3_000);

        onWebView().withElement(findElement(Locator.XPATH, "//h3[contains(.,'Live Terminal')]"))
            .check(webMatches(getText(), containsString("Live Terminal")));
        onWebView().withElement(findElement(Locator.CSS_SELECTOR, "[data-testid='terminal-output-buffer']"))
            .check(webMatches(getText(), containsString("connected:")));
    }
}
