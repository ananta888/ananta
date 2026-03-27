import time
from typing import List, Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import BannedIPDB, LoginAttemptDB, PasswordHistoryDB, RefreshTokenDB, UserDB


class UserRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(UserDB)).all()

    def get_by_username(self, username: str) -> Optional[UserDB]:
        with Session(engine) as session:
            return session.get(UserDB, username)

    def save(self, user: UserDB):
        with Session(engine) as session:
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def delete(self, username: str):
        with Session(engine) as session:
            user = session.get(UserDB, username)
            if user:
                session.delete(user)
                session.commit()
                return True
            return False


class RefreshTokenRepository:
    def get_by_token(self, token: str) -> Optional[RefreshTokenDB]:
        with Session(engine) as session:
            return session.get(RefreshTokenDB, token)

    def save(self, token_obj: RefreshTokenDB):
        with Session(engine) as session:
            session.add(token_obj)
            session.commit()
            session.refresh(token_obj)
            return token_obj

    def delete(self, token: str):
        with Session(engine) as session:
            token_obj = session.get(RefreshTokenDB, token)
            if token_obj:
                session.delete(token_obj)
                session.commit()
                return True
            return False

    def delete_expired(self):
        with Session(engine) as session:
            statement = select(RefreshTokenDB).where(RefreshTokenDB.expires_at < time.time())
            results = session.exec(statement)
            for token_obj in results:
                session.delete(token_obj)
            session.commit()

    def delete_by_username(self, username: str):
        with Session(engine) as session:
            from sqlmodel import delete

            statement = delete(RefreshTokenDB).where(RefreshTokenDB.username == username)
            session.exec(statement)
            session.commit()


class LoginAttemptRepository:
    def get_recent_count(self, ip: str, window_seconds: int = 60) -> int:
        now = time.time()
        with Session(engine) as session:
            statement = select(LoginAttemptDB).where(
                LoginAttemptDB.ip == ip, LoginAttemptDB.timestamp > now - window_seconds
            )
            results = session.exec(statement)
            return len(results.all())

    def record_attempt(self, ip: str):
        attempt = LoginAttemptDB(ip=ip)
        with Session(engine) as session:
            session.add(attempt)
            session.commit()

    def save(self, attempt: LoginAttemptDB):
        with Session(engine) as session:
            session.add(attempt)
            session.commit()
            session.refresh(attempt)
            return attempt

    def delete_by_ip(self, ip: str):
        with Session(engine) as session:
            from sqlmodel import delete

            statement = delete(LoginAttemptDB).where(LoginAttemptDB.ip == ip)
            session.exec(statement)
            session.commit()

    def clear_all(self):
        with Session(engine) as session:
            from sqlmodel import delete

            session.exec(delete(LoginAttemptDB))
            session.commit()

    def delete_old(self, max_age_seconds: int = 86400):
        with Session(engine) as session:
            from sqlmodel import delete

            cutoff = time.time() - max_age_seconds
            statement = delete(LoginAttemptDB).where(LoginAttemptDB.timestamp < cutoff)
            session.exec(statement)
            session.commit()


class BannedIPRepository:
    def is_banned(self, ip: str) -> bool:
        with Session(engine) as session:
            banned = session.get(BannedIPDB, ip)
            if banned:
                if banned.banned_until > time.time():
                    return True
                # Ban abgelaufen, entfernen
                session.delete(banned)
                session.commit()
            return False

    def ban_ip(self, ip: str, duration_seconds: int, reason: str = None):
        with Session(engine) as session:
            banned = session.get(BannedIPDB, ip)
            if banned:
                banned.banned_until = time.time() + duration_seconds
                banned.reason = reason
            else:
                banned = BannedIPDB(ip=ip, banned_until=time.time() + duration_seconds, reason=reason)
            session.add(banned)
            session.commit()

    def delete_by_ip(self, ip: str):
        with Session(engine) as session:
            banned = session.get(BannedIPDB, ip)
            if banned:
                session.delete(banned)
                session.commit()
                return True
            return False

    def delete_expired(self):
        with Session(engine) as session:
            from sqlmodel import delete

            statement = delete(BannedIPDB).where(BannedIPDB.banned_until < time.time())
            session.exec(statement)
            session.commit()


class PasswordHistoryRepository:
    def get_by_username(self, username: str, limit: int = 3) -> List[PasswordHistoryDB]:
        with Session(engine) as session:
            statement = (
                select(PasswordHistoryDB)
                .where(PasswordHistoryDB.username == username)
                .order_by(PasswordHistoryDB.created_at.desc())
                .limit(limit)
            )
            return session.exec(statement).all()

    def save(self, history_entry: PasswordHistoryDB):
        with Session(engine) as session:
            session.add(history_entry)
            session.commit()
            session.refresh(history_entry)

            # Cleanup: Nur die letzten 5 Passwörter behalten
            from sqlmodel import delete

            statement = (
                select(PasswordHistoryDB.id)
                .where(PasswordHistoryDB.username == history_entry.username)
                .order_by(PasswordHistoryDB.created_at.desc())
                .limit(5)
            )
            ids_to_keep = session.exec(statement).all()

            delete_statement = delete(PasswordHistoryDB).where(
                PasswordHistoryDB.username == history_entry.username, PasswordHistoryDB.id.not_in(ids_to_keep)
            )
            session.exec(delete_statement)
            session.commit()

            return history_entry

    def delete_by_username(self, username: str):
        with Session(engine) as session:
            from sqlmodel import delete

            statement = delete(PasswordHistoryDB).where(PasswordHistoryDB.username == username)
            session.exec(statement)
            session.commit()
