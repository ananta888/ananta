package example.auth;

/** Small immutable result type for token verification fixture code. */
public record VerificationResult(boolean allowed, String subject, String reason) {
    public static VerificationResult allowed(String subject) {
        return new VerificationResult(true, subject, "allowed");
    }

    public static VerificationResult denied(String reason) {
        return new VerificationResult(false, "", reason);
    }
}
