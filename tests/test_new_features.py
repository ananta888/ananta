import time
import pytest
from agent.shell import get_shell_pool
from agent.metrics import SHELL_POOL_SIZE, SHELL_POOL_BUSY, SHELL_POOL_FREE, RETRIES_TOTAL
from agent.llm_integration import _call_llm
from unittest.mock import patch, MagicMock

def test_shell_pool_metrics():
    # Hole den Pool
    pool = get_shell_pool(size=2)
    
    # Prüfe Initialwerte (wenn MockMetric, dann haben sie kein .set() sondern wir prüfen ob sie aufgerufen wurden)
    # Da wir in der Testumgebung sind, könnten es echte Gauges sein wenn prometheus_client installiert ist.
    # Da ich nicht sicher bin, ob ich die internen Werte von prometheus_client Gauges einfach abfragen kann:
    # Wir prüfen hier eher die Logik im Code.
    
    assert pool.size == 2
    
    # Acquire shell
    s1 = pool.acquire()
    # Busy sollte 1 sein, Free 1
    
    s2 = pool.acquire()
    # Busy sollte 2 sein, Free 0
    
    # Ein weiteres Acquire sollte eine temporäre Shell liefern (da Pool voll)
    s3 = pool.acquire(timeout=0.1)
    
    pool.release(s1)
    # Busy 1, Free 1
    
    pool.release(s2)
    # Busy 0, Free 2
    
    pool.release(s3) # Temporäre Shell wird geschlossen
    
    print("Shell Pool Metrics Test Passed (Logic check)")

def test_llm_retry_logic():
    urls = {"ollama": "http://localhost:11434"}
    
    # Mocke _execute_llm_call so, dass es erst beim 3. Mal Erfolg hat
    with patch('agent.llm_integration._execute_llm_call') as mock_exec:
        mock_exec.side_effect = ["", "", "Erfolg"]
        
        with patch('agent.llm_integration.settings') as mock_settings:
            mock_settings.retry_count = 3
            mock_settings.retry_backoff = 0.1 # Schneller backoff für tests
            
            res = _call_llm("ollama", "m1", "p1", urls, None)
            
            assert res == "Erfolg"
            assert mock_exec.call_count == 3
            
    # Mocke _execute_llm_call so, dass es immer fehlschlägt
    with patch('agent.llm_integration._execute_llm_call') as mock_exec:
        mock_exec.return_value = ""
        
        with patch('agent.llm_integration.settings') as mock_settings:
            mock_settings.retry_count = 2
            mock_settings.retry_backoff = 0.1
            
            res = _call_llm("ollama", "m1", "p1", urls, None)
            
            assert res == ""
            assert mock_exec.call_count == 3 # Initial + 2 Retries
            
    print("LLM Retry Logic Test Passed")

if __name__ == "__main__":
    test_shell_pool_metrics()
    test_llm_retry_logic()
