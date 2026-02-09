import pytest

def test_security_headers(client):
    response = client.get('/health')
    headers = response.headers

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert "X-XSS-Protection" not in headers
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in headers
    
    csp = headers.get("Content-Security-Policy")
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "connect-src 'self' *" not in csp
    assert "frame-ancestors 'none'" in csp

def test_hsts_header_on_secure_request(client):
    # In Flask's test client we can simulate a secure request
    response = client.get('/health', base_url='https://localhost')
    headers = response.headers
    
    assert "Strict-Transport-Security" in headers
    assert "preload" in headers.get("Strict-Transport-Security")

def test_hsts_header_on_proxy_secure_request(client):
    # Simulate a request behind a proxy with X-Forwarded-Proto
    response = client.get('/health', headers={"X-Forwarded-Proto": "https"})
    headers = response.headers
    
    assert "Strict-Transport-Security" in headers
    assert "max-age=31536000; includeSubDomains; preload" == headers.get("Strict-Transport-Security")


def test_csp_default_policy_disables_inline(client):
    response = client.get('/health')
    csp = response.headers.get("Content-Security-Policy", "")
    assert "script-src 'self';" in csp
    assert "style-src 'self';" in csp
    assert "'unsafe-inline'" not in csp


def test_csp_swagger_policy_allows_inline(client):
    response = client.get('/apidocs/')
    csp = response.headers.get("Content-Security-Policy", "")
    assert "script-src 'self' 'unsafe-inline';" in csp
    assert "style-src 'self' 'unsafe-inline';" in csp
