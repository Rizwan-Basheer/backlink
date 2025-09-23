"""Notification helper functions."""

from __future__ import annotations

from sqlmodel import Session, select

from ..models import Notification


class NotificationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, message: str, *, type: str = "info") -> Notification:
        notification = Notification(message=message, type=type)
        self.session.add(notification)
        self.session.flush()
        return notification

    def list_unread(self) -> list[Notification]:
        statement = select(Notification).where(Notification.is_read.is_(False))
        return list(self.session.exec(statement))

    def list_recent(self, *, limit: int = 20) -> list[Notification]:
        statement = (
            select(Notification)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
        return list(self.session.exec(statement))

    def mark_read(self, notification_id: int) -> Notification:
        notification = self.session.get(Notification, notification_id)
        if not notification:
            raise ValueError("notification not found")
        notification.is_read = True
        self.session.add(notification)
        self.session.flush()
        return notification


__all__ = ["NotificationService"]
