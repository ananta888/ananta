package example.security;

public final class TokenVerifier {
    public boolean verify(String bearerToken, String issuer) {
        if (bearerToken == null || bearerToken.isBlank()) {
            return false;
        }
        if (issuer == null || issuer.isBlank()) {
            return false;
        }
        return issuer.startsWith("https://issuer.example/");
    }
}
