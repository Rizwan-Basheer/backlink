"""Analytical helpers exposed in the admin dashboard."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlmodel import Session, func, select

from ..models import Category, Execution, ExecutionStatus, Recipe


@dataclass
class CategorySummary:
    category: str
    recipe_count: int


@dataclass
class ExecutionSummary:
    success: int
    failure: int
    pending: int

    @property
    def total(self) -> int:
        return self.success + self.failure + self.pending

    @property
    def success_ratio(self) -> float:
        if not self.total:
            return 0.0
        return self.success / self.total


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def total_backlinks(self) -> int:
        statement = select(func.count()).where(Execution.status == ExecutionStatus.SUCCESS)
        return self.session.exec(statement).one()

    def recipes_per_category(self) -> list[CategorySummary]:
        statement = (
            select(Category.name, func.count(Recipe.id))
            .join(Recipe, Recipe.category_id == Category.id)
            .group_by(Category.name)
        )
        rows = self.session.exec(statement).all()
        return [CategorySummary(category=name, recipe_count=count) for name, count in rows]

    def execution_summary(self) -> ExecutionSummary:
        statement = select(Execution.status, func.count()).group_by(Execution.status)
        rows = self.session.exec(statement).all()
        counter = Counter()
        for status, count in rows:
            counter[status] = count
        return ExecutionSummary(
            success=counter.get(ExecutionStatus.SUCCESS, 0),
            failure=counter.get(ExecutionStatus.FAILURE, 0),
            pending=counter.get(ExecutionStatus.PENDING, 0),
        )

    def historical_stats(self, *, days: int = 30) -> dict[str, list[tuple[str, int]]]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        statement = select(Execution).where(Execution.started_at >= cutoff)
        executions = self.session.exec(statement).all()
        by_day: defaultdict[str, Counter] = defaultdict(Counter)
        for execution in executions:
            day = execution.started_at.strftime("%Y-%m-%d")
            by_day[day][execution.status.value] += 1
        success = [(day, counts.get(ExecutionStatus.SUCCESS.value, 0)) for day, counts in sorted(by_day.items())]
        failure = [(day, counts.get(ExecutionStatus.FAILURE.value, 0)) for day, counts in sorted(by_day.items())]
        return {"success": success, "failure": failure}


__all__ = ["AnalyticsService", "CategorySummary", "ExecutionSummary"]
