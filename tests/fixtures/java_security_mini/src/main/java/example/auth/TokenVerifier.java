package example.auth;

import java.time.Instant;
import java.util.Map;
import java.util.Objects;

/**
 * Small security-oriented Java reference fixture used by Ananta tests.
 * It is intentionally tiny, but models the same kind of auth boundary that
 * a larger Keycloak-like reference profile would be selected for.
 */
public final class TokenVerifier {
    private final PolicyService policyService;

    public TokenVerifier(PolicyService policyService) {
        this.policyService = Objects.requireNonNull(policyService, "policyService");
    }

    public VerificationResult verifyBearerToken(String token, Map<String, String> claims) {
        if (token == null || token.isBlank()) {
            return VerificationResult.denied("missing_token");
        }
        if (!claims.containsKey("subject") || !claims.containsKey("issuer")) {
            return VerificationResult.denied("missing_required_claim");
        }
        if (!policyService.isIssuerAllowed(claims.get("issuer"))) {
            return VerificationResult.denied("issuer_not_allowed");
        }
        if (isExpired(claims.get("expires_at"))) {
            return VerificationResult.denied("token_expired");
        }
        return VerificationResult.allowed(claims.get("subject"));
    }

    private boolean isExpired(String expiresAt) {
        if (expiresAt == null || expiresAt.isBlank()) {
            return true;
        }
        return Instant.parse(expiresAt).isBefore(Instant.now());
    }
}
