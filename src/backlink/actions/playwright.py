"""Wrapper around Playwright for running recipe actions."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import DEFAULT_TIMEOUT_MS, HEADLESS

try:  # pragma: no cover - optional dependency
    from playwright.async_api import Page, async_playwright
except Exception:  # pragma: no cover - import guard
    Page = None  # type: ignore
    async_playwright = None  # type: ignore


Troubleshooter = Callable[[dict[str, Any]], Mapping[str, Any] | None]


@dataclass
class PlaywrightRunResult:
    last_screenshot: str | None = None
    attempts: int = 0


class PlaywrightActionRunner:
    """Run recipe actions using Playwright if available, otherwise log steps."""

    def __init__(self, *, headless: bool | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        self.headless = headless if headless is not None else HEADLESS
        self.timeout_ms = timeout_ms

    def run(
        self,
        actions: Sequence[Mapping[str, Any]],
        *,
        logger: logging.Logger,
        config: Mapping[str, Any] | None = None,
        troubleshoot: Troubleshooter | None = None,
        max_attempts: int = 1,
        screenshot_dir: Path | None = None,
    ) -> PlaywrightRunResult:
        config = config or {}
        if async_playwright is None:
            logger.warning("Playwright not available - falling back to dry-run mode")
            self._run_stub(actions, logger=logger)
            return PlaywrightRunResult(last_screenshot=None, attempts=len(actions))
        return asyncio.run(
            self._run_async(
                actions,
                logger=logger,
                config=dict(config),
                troubleshoot=troubleshoot,
                max_attempts=max(1, int(max_attempts)),
                screenshot_dir=screenshot_dir,
            )
        )

    async def _run_async(
        self,
        actions: Sequence[Mapping[str, Any]],
        *,
        logger: logging.Logger,
        config: Mapping[str, Any],
        troubleshoot: Troubleshooter | None,
        max_attempts: int,
        screenshot_dir: Path | None,
    ) -> PlaywrightRunResult:  # pragma: no cover - network interactions
        result = PlaywrightRunResult()
        headless = bool(config.get("headless", self.headless))
        timeout_ms = int(config.get("timeout_ms", self.timeout_ms))
        delay_ms = int(config.get("per_action_delay_ms", 0))
        jitter_ms = int(config.get("random_jitter_ms", 0))

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)
            for index, action in enumerate(actions, start=1):
                attempts = 0
                current = dict(action)
                while True:
                    attempts += 1
                    try:
                        await self._execute_action(page, current, logger)
                        await self._apply_delay(page, delay_ms, jitter_ms)
                        break
                    except Exception as exc:  # pragma: no cover - network interactions
                        logger.warning(
                            "Action %s failed (attempt %s/%s): %s",
                            index,
                            attempts,
                            max_attempts,
                            exc,
                        )
                        if attempts >= max_attempts:
                            screenshot_path = await self._capture_screenshot(
                                page, screenshot_dir, index, attempts
                            )
                            result.last_screenshot = screenshot_path
                            raise
                        suggestion: Mapping[str, Any] | None = None
                        if troubleshoot:
                            dom = await self._dom_excerpt(page, current.get("selector"))
                            suggestion = troubleshoot(
                                {
                                    "error": str(exc),
                                    "action": current,
                                    "dom": dom,
                                    "url": page.url if page else None,
                                    "attempt": attempts,
                                }
                            )
                        if suggestion and suggestion.get("selector"):
                            current = dict(current)
                            current["selector"] = suggestion.get("selector")
                            logger.info(
                                "Retrying action %s with selector suggested by troubleshooter: %s",
                                index,
                                current["selector"],
                            )
                            continue
                        screenshot_path = await self._capture_screenshot(
                            page, screenshot_dir, index, attempts
                        )
                        result.last_screenshot = screenshot_path
                        raise
                result.attempts += attempts
            await context.close()
            await browser.close()
        return result

    async def _execute_action(
        self, page: "Page", action: Mapping[str, Any], logger: logging.Logger
    ) -> None:  # pragma: no cover - network interactions
        kind = action.get("action")
        selector = action.get("selector")
        value = action.get("value")
        wait_for = action.get("wait_for")

        logger.info(
            "Executing %s - selector=%s value=%s",
            kind,
            selector,
            self._redact(value) if isinstance(value, str) else value,
        )
        if kind == "goto":
            await page.goto(str(value), wait_until="networkidle")
        elif kind == "fill":
            await page.fill(str(selector), str(value))
        elif kind == "click":
            await page.click(str(selector))
        elif kind == "wait_for_selector":
            await page.wait_for_selector(str(selector))
        elif kind == "wait":
            await page.wait_for_timeout(int(float(value) * 1000))
        else:
            logger.warning("Unsupported action '%s' - skipping", kind)

        if wait_for:
            await page.wait_for_timeout(int(float(wait_for) * 1000))

        if action.get("screenshot"):
            path = action.get("screenshot_path")
            await page.screenshot(path=path or None)

    async def _apply_delay(self, page: "Page", delay_ms: int, jitter_ms: int) -> None:
        wait = delay_ms
        if jitter_ms:
            wait += random.randint(0, max(jitter_ms, 0))
        if wait > 0:
            await page.wait_for_timeout(wait)

    async def _capture_screenshot(
        self,
        page: "Page",
        directory: Path | None,
        index: int,
        attempt: int,
    ) -> str | None:
        if not directory:
            return None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"failure_action_{index}_attempt_{attempt}.png"
            await page.screenshot(path=str(path))
            return str(path)
        except Exception:  # pragma: no cover - best effort
            return None

    async def _dom_excerpt(self, page: "Page", selector: Any) -> str:
        try:
            if selector:
                handle = await page.query_selector(str(selector))
                if handle:
                    html = await handle.inner_html()
                    return html[:2000]
            content = await page.content()
            return content[:2000]
        except Exception:
            return ""

    def _run_stub(self, actions: Sequence[Mapping[str, Any]], *, logger: logging.Logger) -> None:
        for index, action in enumerate(actions, start=1):
            logger.info(
                "[%s] %s -> selector=%s value=%s",
                index,
                action.get("action"),
                action.get("selector"),
                self._redact(action.get("value")),
            )

    @staticmethod
    def _redact(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        lowered = value.lower()
        if any(token in lowered for token in ("password", "secret", "token")):
            return "***"
        return value


__all__ = ["PlaywrightActionRunner", "PlaywrightRunResult"]
