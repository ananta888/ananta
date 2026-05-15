import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from config import settings
from models.user import User
from schemas.user import UserCreate

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def _hash_password(self, password: str) -> str:
        return pwd_context.hash(password)

    def _verify_password(self, plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    def _create_token(self, user: User) -> str:
        expire = datetime.now(timezone.utc) + timedelta(seconds=settings.access_token_expire_seconds)
        payload = {
            "sub": user.username,
            "user_id": str(user.id),
            "role": user.role.value,
            "exp": expire,
        }
        return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)

    def register(self, payload: UserCreate) -> User:
        existing = self.db.query(User).filter(
            (User.username == payload.username) | (User.email == payload.email)
        ).first()
        if existing:
            raise ValueError("Username or email already registered")
        user = User(
            username=payload.username,
            email=payload.email,
            hashed_password=self._hash_password(payload.password),
            role=payload.role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def authenticate(self, username: str, password: str) -> tuple[User, str] | None:
        user = self.db.query(User).filter(User.username == username).first()
        if not user or not self._verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user, self._create_token(user)
