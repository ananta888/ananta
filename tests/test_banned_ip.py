import pytest
import time
from agent.ai_agent import create_app
from agent.repository import login_attempt_repo, banned_ip_repo
from agent.db_models import LoginAttemptDB, BannedIPDB
from agent.database import engine
from sqlmodel import Session, delete

@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    
    login_attempt_repo.clear_all()
    with Session(engine) as session:
        session.exec(delete(BannedIPDB))
        session.commit()
    
    with app.test_client() as client:
        yield client

def test_ip_banning_after_50_attempts(client):
    ip = "127.0.0.1"
    now = time.time()
    
    # Simuliere 50 Login-Versuche in der letzten Stunde
    for i in range(50):
        login_attempt_repo.save(LoginAttemptDB(ip=ip, timestamp=now - 60))
        
    # Der 51. Versuch via API sollte den Ban auslösen
    response = client.post("/login", json={"username": "testuser", "password": "wrong-password"})
    
    # Da get_recent_count >= 50 (49 + 1) wird is_rate_limited True zurückgeben
    assert response.status_code == 429
    
    # Prüfen ob IP in der Banned-Tabelle ist
    assert banned_ip_repo.is_banned(ip) == True
    
def test_banned_ip_remains_banned(client):
    ip = "1.2.3.4"
    banned_ip_repo.ban_ip(ip, duration_seconds=60)
    
    # Da der TestClient standardmäßig 127.0.0.1 nutzt, müssen wir den Check direkt machen 
    # oder die IP im Request simulieren, was schwierig ist ohne Proxy headers.
    # Wir prüfen das Repository direkt.
    assert banned_ip_repo.is_banned(ip) == True
    
    # Ban abgelaufen simulieren
    with Session(engine) as session:
        b = session.get(BannedIPDB, ip)
        b.banned_until = time.time() - 10
        session.add(b)
        session.commit()
        
    assert banned_ip_repo.is_banned(ip) == False
