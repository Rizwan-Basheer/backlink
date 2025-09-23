"""Category management services."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from ..models import Category, CategoryRequest, CategoryRequestStatus


class CategoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_categories(self, *, include_inactive: bool = False) -> list[Category]:
        statement = select(Category)
        if not include_inactive:
            statement = statement.where(Category.is_active.is_(True))
        return list(self.session.exec(statement))

    def create_category(self, name: str, description: str | None = None) -> Category:
        category = Category(name=name, description=description)
        self.session.add(category)
        self.session.flush()
        return category

    def update_category(
        self,
        category_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> Category:
        category = self.session.get(Category, category_id)
        if not category:
            raise ValueError("category not found")
        if name is not None:
            category.name = name
        if description is not None:
            category.description = description
        if is_active is not None:
            category.is_active = is_active
        self.session.add(category)
        self.session.flush()
        return category

    def delete_category(self, category_id: int) -> None:
        category = self.session.get(Category, category_id)
        if not category:
            raise ValueError("category not found")
        if category.recipes:
            raise ValueError("category contains recipes and cannot be deleted")
        self.session.delete(category)
        self.session.flush()

    # Requests -----------------------------------------------------------
    def create_request(self, *, requested_by: int, name: str, reason: str | None = None) -> CategoryRequest:
        request = CategoryRequest(requested_by_id=requested_by, name=name, reason=reason)
        self.session.add(request)
        self.session.flush()
        return request

    def list_requests(self, *, status: CategoryRequestStatus | None = None) -> list[CategoryRequest]:
        statement = select(CategoryRequest)
        if status:
            statement = statement.where(CategoryRequest.status == status)
        return list(self.session.exec(statement))

    def approve_request(self, request_id: int, *, description: str | None = None) -> Category:
        request = self.session.get(CategoryRequest, request_id)
        if not request:
            raise ValueError("request not found")
        request.status = CategoryRequestStatus.APPROVED
        request.decision_at = datetime.utcnow()
        category = self.create_category(request.name, description)
        self.session.add(request)
        self.session.flush()
        return category

    def reject_request(self, request_id: int, *, reason: str | None = None) -> CategoryRequest:
        request = self.session.get(CategoryRequest, request_id)
        if not request:
            raise ValueError("request not found")
        request.status = CategoryRequestStatus.REJECTED
        request.decision_at = datetime.utcnow()
        if reason:
            request.reason = reason
        self.session.add(request)
        self.session.flush()
        return request


__all__ = ["CategoryService"]
