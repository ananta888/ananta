import pytest
import json

def test_csp_header_report_uri(client):
    """Prüft ob report-uri im CSP Header vorhanden ist."""
    response = client.get('/health')
    csp = response.headers.get("Content-Security-Policy")
    assert "report-uri /api/system/csp-report;" in csp

def test_csp_report_endpoint(client):
    """Prüft den CSP-Report Endpoint."""
    report_data = {
        "csp-report": {
            "document-uri": "http://localhost/test",
            "referrer": "",
            "violated-directive": "img-src",
            "effective-directive": "img-src",
            "original-policy": "default-src 'self'; report-uri /api/system/csp-report;",
            "disposition": "enforce",
            "blocked-uri": "http://evil.com/image.png",
            "line-number": 10,
            "column-number": 5,
            "source-file": "test.js",
            "status-code": 200,
            "script-sample": ""
        }
    }
    
    # POST an den Endpoint senden
    response = client.post(
        '/api/system/csp-report',
        data=json.dumps(report_data),
        content_type='application/json'
    )
    
    assert response.status_code == 204

def test_csp_report_rate_limit(client):
    """Prüft das Rate-Limiting am CSP-Report Endpoint."""
    report_data = {"csp-report": {"blocked-uri": "test"}}
    
    # 10 Anfragen sind erlaubt (inklusive der aus dem vorherigen Test, falls der Client-State persistiert)
    # Wir machen hier einfach so viele Anfragen bis wir 429 bekommen, und prüfen ob das passiert.
    status_codes = []
    for _ in range(12):
        res = client.post('/api/system/csp-report', data=json.dumps(report_data), content_type='application/json')
        status_codes.append(res.status_code)
        
    assert 204 in status_codes
    assert 429 in status_codes
