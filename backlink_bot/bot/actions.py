"""Action definitions and executor built on Playwright."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from ..utils.logging import get_logger
from .. import config

logger = get_logger("backlink.actions")


class ActionType(str, Enum):
    GOTO = "goto"
    CLICK = "click"
    FILL = "fill"
    WAIT_FOR = "wait_for"
    WAIT = "wait"
    SELECT_OPTION = "select_option"
    SCREENSHOT = "screenshot"


@dataclass
class BrowserAction:
    """Represents a single recorded action."""

    type: ActionType
    selector: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None
    wait_for: Optional[str] = None
    screenshot_path: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"type": self.type.value}
        if self.selector:
            payload["selector"] = self.selector
        if self.value is not None:
            payload["value"] = self.value
        if self.description:
            payload["description"] = self.description
        if self.wait_for:
            payload["wait_for"] = self.wait_for
        if self.screenshot_path:
            payload["screenshot_path"] = self.screenshot_path
        return payload

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "BrowserAction":
        return cls(
            type=ActionType(payload["type"]),
            selector=payload.get("selector"),
            value=payload.get("value"),
            description=payload.get("description"),
            wait_for=payload.get("wait_for"),
            screenshot_path=payload.get("screenshot_path"),
        )


class ActionExecutor:
    """Execute recorded actions using Playwright."""

    def __init__(self, headless: bool | None = None) -> None:
        self.headless = config.RUN_HEADLESS if headless is None else headless

    async def run_actions(
        self,
        actions: Iterable[BrowserAction],
        variables: Optional[Dict[str, Any]] = None,
        screenshot_dir: Optional[str] = None,
    ) -> List[str]:
        """Execute actions and return log lines."""

        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Playwright is required to execute recipes. Install the 'browser' extra"
            ) from exc

        logs: List[str] = []
        screenshot_dir_path = config.SCREENSHOT_DIR if screenshot_dir is None else config.SCREENSHOT_DIR / screenshot_dir
        screenshot_dir_path.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            page = await browser.new_page()
            for action in actions:
                logger.info("Executing %s", action)
                logs.append(f"Executing {action.type.value}")
                if action.type == ActionType.GOTO:
                    assert action.value, "Goto requires a target URL"
                    await page.goto(action.value, wait_until="networkidle")
                elif action.type == ActionType.CLICK:
                    assert action.selector, "Click requires a selector"
                    await page.click(action.selector)
                elif action.type == ActionType.FILL:
                    assert action.selector, "Fill requires a selector"
                    await page.fill(action.selector, action.value or "")
                elif action.type == ActionType.WAIT_FOR:
                    assert action.selector, "Wait_for requires a selector"
                    await page.wait_for_selector(action.selector)
                elif action.type == ActionType.WAIT:
                    await asyncio.sleep(float(action.value or 1))
                elif action.type == ActionType.SELECT_OPTION:
                    assert action.selector and action.value
                    await page.select_option(action.selector, action.value)
                elif action.type == ActionType.SCREENSHOT:
                    path = screenshot_dir_path / (action.value or "screenshot.png")
                    await page.screenshot(path=str(path))
                else:  # pragma: no cover - safeguard
                    raise ValueError(f"Unknown action {action.type}")

                if action.wait_for:
                    await page.wait_for_selector(action.wait_for)

            await browser.close()
        return logs

    def execute_sync(
        self,
        actions: Iterable[BrowserAction],
        variables: Optional[Dict[str, Any]] = None,
        screenshot_dir: Optional[str] = None,
    ) -> List[str]:
        """Convenience wrapper to run actions from synchronous code."""

        return asyncio.run(self.run_actions(actions, variables=variables, screenshot_dir=screenshot_dir))


__all__ = ["BrowserAction", "ActionExecutor", "ActionType"]
