package io.ananta.eclipse.runtime.security;

import java.util.Objects;
import java.util.regex.Pattern;

public final class TokenRedaction {
    private static final Pattern INLINE_SECRET_PATTERN = Pattern.compile(
            "(?i)(token|secret|password|private[_-]?key|credential|api[_-]?key)[=:]\\S+"
    );

    private TokenRedaction() {
    }

    public static String redactSensitiveText(String value) {
        String text = Objects.toString(value, "");
        return INLINE_SECRET_PATTERN.matcher(text).replaceAll("$1=***");
    }

    public static boolean containsSensitiveKey(String key) {
        String normalized = Objects.toString(key, "").trim().toLowerCase();
        return normalized.contains("token")
                || normalized.contains("secret")
                || normalized.contains("password")
                || normalized.contains("credential")
                || normalized.contains("private_key")
                || normalized.contains("api_key")
                || normalized.contains("api-key");
    }
}
