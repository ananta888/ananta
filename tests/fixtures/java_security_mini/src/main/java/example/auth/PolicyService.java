package example.auth;

import java.util.Set;

/** Policy boundary fixture for Java reference retrieval tests. */
public final class PolicyService {
    private final Set<String> trustedIssuers;

    public PolicyService(Set<String> trustedIssuers) {
        this.trustedIssuers = Set.copyOf(trustedIssuers);
    }

    public boolean isIssuerAllowed(String issuer) {
        return trustedIssuers.contains(issuer);
    }

    public boolean canAccessAdminApi(String subject, String role) {
        return subject != null && !subject.isBlank() && "admin".equals(role);
    }
}
