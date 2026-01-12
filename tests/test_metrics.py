
import pytest
import os
import time

def test_system_stats_metrics(client, app):
    """Testet ob der /stats Endpunkt valide CPU und RAM Metriken liefert."""
    # Auth deaktivieren oder Token nutzen
    app.config["AGENT_TOKEN"] = "test-token"
    headers = {"Authorization": "Bearer test-token"}
    
    # Erste Anfrage kann 0% CPU liefern, wir machen zwei mit kurzem Abstand
    client.get('/stats', headers=headers)
    time.sleep(0.1)
    response = client.get('/stats', headers=headers)
    
    assert response.status_code == 200
    
    data = response.json
    assert "resources" in data
    res = data["resources"]
    
    assert "cpu_percent" in res
    assert "ram_bytes" in res
    
    # Plausibilitäts-Checks
    assert isinstance(res["cpu_percent"], (int, float))
    # CPU sollte zwischen 0 und 100 liegen. 
    # Bei manchen Systemen kann es kurzzeitig > 100 sein (Multi-Core), 
    # aber psutil.Process(os.getpid()).cpu_percent() sollte 0-100 pro Core sein 
    # oder 0-100*Cores. In system.py wird interval=None genutzt.
    assert 0 <= res["cpu_percent"] <= 100 * os.cpu_count()
    
    assert isinstance(res["ram_bytes"], int)
    assert res["ram_bytes"] > 0
    # RAM sollte plausibel sein (> 1MB)
    assert res["ram_bytes"] > 1024 * 1024 

def test_prometheus_metrics(client, app):
    """Testet ob der /metrics Endpunkt (Prometheus) die Werte enthält."""
    response = client.get('/metrics')
    assert response.status_code == 200
    content = response.data.decode('utf-8')
    
    # Wir prüfen ob die Metriken im Prometheus Format vorhanden sind
    assert "process_cpu_usage_percent" in content
    assert "process_ram_usage_bytes" in content
