"""Authentication and user management helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from passlib.context import CryptContext
from sqlmodel import Session, select

from ..models import Role, User

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""

    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a stored hash."""

    try:
        return _pwd_context.verify(password, hashed)
    except ValueError:  # pragma: no cover - corrupted hash
        return False


@dataclass
class AuthenticatedUser:
    id: int
    email: str
    name: str
    role: Role


class AuthService:
    """Persistence-backed user operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # Queries -----------------------------------------------------------------
    def get_user_by_email(self, email: str) -> Optional[User]:
        statement = select(User).where(User.email == email.lower())
        return self.session.exec(statement).first()

    def get(self, user_id: int) -> Optional[User]:
        return self.session.get(User, user_id)

    # Mutations ----------------------------------------------------------------
    def create_user(
        self,
        *,
        email: str,
        name: str,
        password: str,
        role: Role = Role.USER,
        is_active: bool = True,
    ) -> User:
        existing = self.get_user_by_email(email)
        if existing:
            raise ValueError("user already exists")
        user = User(
            email=email.lower(),
            name=name,
            role=role,
            hashed_password=hash_password(password),
            is_active=is_active,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def update_password(self, user: User, password: str) -> User:
        user.hashed_password = hash_password(password)
        self.session.add(user)
        self.session.flush()
        return user

    def seed_admin(self, email: str, *, name: str, password: str) -> User:
        """Ensure an administrative user exists for bootstrap environments."""

        email = email.lower()
        user = self.get_user_by_email(email)
        if user:
            user.role = Role.ADMIN
            user.name = name
            self.update_password(user, password)
        else:
            user = self.create_user(email=email, name=name, password=password, role=Role.ADMIN)
        return user

    # Authentication -----------------------------------------------------------
    def authenticate(self, email: str, password: str) -> Optional[AuthenticatedUser]:
        user = self.get_user_by_email(email)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return AuthenticatedUser(id=user.id, email=user.email, name=user.name, role=user.role)


__all__ = ["AuthService", "AuthenticatedUser", "hash_password", "verify_password"]
