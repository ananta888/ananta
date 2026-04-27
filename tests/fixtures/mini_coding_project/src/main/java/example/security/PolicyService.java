package example.security;

public final class PolicyService {
    public boolean isAdmin(String role) {
        return "admin".equals(role);
    }
}
