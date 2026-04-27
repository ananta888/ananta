package example.admin;

import example.auth.PolicyService;
import example.auth.TokenVerifier;
import example.auth.VerificationResult;
import java.util.Map;

/** Admin API boundary fixture for Java reference retrieval tests. */
public final class AdminResource {
    private final TokenVerifier tokenVerifier;
    private final PolicyService policyService;

    public AdminResource(TokenVerifier tokenVerifier, PolicyService policyService) {
        this.tokenVerifier = tokenVerifier;
        this.policyService = policyService;
    }

    public String rotateClientSecret(String bearerToken, Map<String, String> claims) {
        VerificationResult result = tokenVerifier.verifyBearerToken(bearerToken, claims);
        if (!result.allowed()) {
            return "denied:" + result.reason();
        }
        if (!policyService.canAccessAdminApi(result.subject(), claims.get("role"))) {
            return "denied:admin_role_required";
        }
        return "secret-rotation-approved";
    }
}
