"""Utilities for managing recurring recipe executions."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, select

from ..models import RecipeSchedule, ScheduleFrequency


class SchedulingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def schedule_recipe(
        self,
        *,
        recipe_id: int | None = None,
        category_id: int | None = None,
        frequency: ScheduleFrequency,
        start_at: datetime | None = None,
    ) -> RecipeSchedule:
        if not recipe_id and not category_id:
            raise ValueError("either recipe_id or category_id must be provided")
        schedule = RecipeSchedule(
            recipe_id=recipe_id,
            category_id=category_id,
            frequency=frequency,
            next_run=start_at or datetime.utcnow(),
        )
        self.session.add(schedule)
        self.session.flush()
        return schedule

    def due_schedules(self, *, now: datetime | None = None) -> list[RecipeSchedule]:
        current = now or datetime.utcnow()
        statement = select(RecipeSchedule).where(
            RecipeSchedule.is_active.is_(True), RecipeSchedule.next_run <= current
        )
        return list(self.session.exec(statement))

    def mark_executed(self, schedule: RecipeSchedule, *, executed_at: datetime | None = None) -> RecipeSchedule:
        executed = executed_at or datetime.utcnow()
        schedule.next_run = self._calculate_next_run(schedule.frequency, executed)
        self.session.add(schedule)
        self.session.flush()
        return schedule

    def cancel_schedule(self, schedule_id: int) -> None:
        schedule = self.session.get(RecipeSchedule, schedule_id)
        if not schedule:
            raise ValueError("schedule not found")
        schedule.is_active = False
        self.session.add(schedule)
        self.session.flush()

    def _calculate_next_run(self, frequency: ScheduleFrequency, start: datetime) -> datetime:
        if frequency == ScheduleFrequency.DAILY:
            return start + timedelta(days=1)
        if frequency == ScheduleFrequency.WEEKLY:
            return start + timedelta(weeks=1)
        if frequency == ScheduleFrequency.MONTHLY:
            return start + timedelta(days=30)
        raise ValueError(f"unknown frequency {frequency}")


__all__ = ["SchedulingService"]
