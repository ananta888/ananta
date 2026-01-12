from sqlmodel import SQLModel, create_engine, Session, select
from agent.config import settings
import os
import logging

# Datenbank-URL aus Umgebungsvariable oder Standard (SQLite als Fallback für Tests/lokal)
# Für Produktion wird POSTGRES_URL erwartet.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    # Fallback auf SQLite im data_dir
    db_path = os.path.join(settings.data_dir, "ananta.db")
    DATABASE_URL = f"sqlite:///{db_path}"

engine = create_engine(DATABASE_URL, echo=False)

def init_db():
    import agent.db_models
    SQLModel.metadata.create_all(engine)
    ensure_default_user()

def ensure_default_user():
    from agent.db_models import UserDB
    from werkzeug.security import generate_password_hash
    
    with Session(engine) as session:
        # Prüfen ob bereits Benutzer existieren
        statement = select(UserDB)
        existing_user = session.exec(statement).first()
        
        if not existing_user:
            admin_user = UserDB(
                username="admin",
                password_hash=generate_password_hash("admin"),
                role="admin"
            )
            session.add(admin_user)
            session.commit()
            logging.info("INITIAL USER CREATED: username='admin', password='admin' (PLEASE CHANGE IMMEDIATELY)")
            # Auch auf stdout ausgeben, damit es in den Logs auffällt
            print("\n" + "="*50)
            print("INITIAL USER CREATED")
            print("Username: admin")
            print("Password: admin")
            print("Role:     admin")
            print("="*50 + "\n")
        else:
            logging.info(f"Database already contains users. Initial user 'admin' not created.")

def get_session():
    with Session(engine) as session:
        yield session
