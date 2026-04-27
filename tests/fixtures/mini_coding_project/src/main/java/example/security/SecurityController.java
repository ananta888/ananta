package example.security;

public final class SecurityController {
    private final TokenVerifier tokenVerifier;
    private final PolicyService policyService;

    public SecurityController(TokenVerifier tokenVerifier, PolicyService policyService) {
        this.tokenVerifier = tokenVerifier;
        this.policyService = policyService;
    }

    public String rotateSecret(String bearerToken, String issuer, String role) {
        if (!tokenVerifier.verify(bearerToken, issuer)) {
            return "denied:invalid_token";
        }
        if (!policyService.isAdmin(role)) {
            return "denied:admin_required";
        }
        return "ok:secret_rotated";
    }
}
