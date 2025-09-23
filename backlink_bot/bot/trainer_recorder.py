"""Interactive trainer recorder powered by Playwright."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..utils.logging import get_logger

logger = get_logger("backlink.trainer.recorder")


RecordedType = Literal[
    "goto",
    "click",
    "fill",
    "select_option",
    "wait_for",
    "wait",
    "screenshot",
]


@dataclass
class RecordedAction:
    """Action captured during a recording session."""

    type: RecordedType
    selector: str | None = None
    url: str | None = None
    value: str | None = None
    description: str | None = None
    wait_for: str | None = None
    meta: dict[str, Any] | None = field(default_factory=dict)


@dataclass
class RecordingResult:
    """Aggregate recording output."""

    actions: list[RecordedAction]
    screenshots: list[Path]


_INIT_SCRIPT = """
(() => {
  const config = __CONFIG__;
  const preferred = __PREFERRED__;
  const globalKey = '_trainerRecordEvent';
  if (!window[globalKey]) {
    console.warn('Trainer recorder binding missing');
    return;
  }
  const now = () => Date.now() / 1000;
  const cleanText = (value) => {
    if (!value) return '';
    return value.replace(/\s+/g, ' ').trim();
  };
  const cssEscape = (value) => {
    if (window.CSS && window.CSS.escape) {
      return window.CSS.escape(value);
    }
    return value.replace(/([!"#$%&'()*+,./:;<=>?@\[\]^`{|}~])/g, '\\$1');
  };
  const suitableClass = (className) => {
    if (!className) return false;
    if (className.length > 40) return false;
    if (/\d{4,}/.test(className)) return false;
    return /^[a-zA-Z][\w-]*$/.test(className);
  };
  const descriptor = (element) => {
    if (!element || !(element instanceof Element)) return null;
    for (const attr of preferred) {
      const value = element.getAttribute(attr);
      if (value) {
        if (attr === 'id') {
          return '#' + cssEscape(value);
        }
        return '[' + attr + '="' + cssEscape(value) + '"]';
      }
    }
    const idValue = element.getAttribute('id');
    if (idValue) {
      return '#' + cssEscape(idValue);
    }
    const nameValue = element.getAttribute('name');
    if (nameValue) {
      return element.tagName.toLowerCase() + '[name="' + cssEscape(nameValue) + '"]';
    }
    const roleValue = element.getAttribute('role');
    if (roleValue) {
      return element.tagName.toLowerCase() + '[role="' + cssEscape(roleValue) + '"]';
    }
    const ariaValue = element.getAttribute('aria-label');
    if (ariaValue) {
      return element.tagName.toLowerCase() + '[aria-label="' + cssEscape(ariaValue) + '"]';
    }
    const classes = Array.from(element.classList || []).filter(suitableClass);
    if (classes.length) {
      return element.tagName.toLowerCase() + '.' + classes.join('.');
    }
    const type = element.getAttribute('type');
    if (type) {
      return element.tagName.toLowerCase() + '[type="' + cssEscape(type) + '"]';
    }
    return element.tagName.toLowerCase();
  };
  const buildSelector = (element) => {
    if (!element || !(element instanceof Element)) return null;
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && parts.length < 4) {
      const part = descriptor(current);
      if (!part) break;
      parts.unshift(part);
      if (part.startsWith('#') || part.startsWith('[')) break;
      current = current.parentElement;
    }
    return parts.join(' ');
  };
  const labelFor = (element) => {
    if (!element || !(element instanceof Element)) return '';
    const aria = element.getAttribute('aria-label');
    if (aria) return cleanText(aria);
    const labelled = element.getAttribute('aria-labelledby');
    if (labelled) {
      const texts = labelled.split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => cleanText(node.textContent));
      if (texts.length) return texts.join(' ');
    }
    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') {
      const id = element.getAttribute('id');
      if (id) {
        const label = document.querySelector('label[for="' + cssEscape(id) + '"]');
        if (label) return cleanText(label.textContent);
      }
      let parent = element.parentElement;
      while (parent && parent !== document.body) {
        if (parent.tagName === 'LABEL') {
          return cleanText(parent.textContent);
        }
        parent = parent.parentElement;
      }
    }
    return cleanText(element.textContent || '');
  };
  const send = (payload) => {
    try {
      window[globalKey](payload);
    } catch (error) {
      console.error('Recorder dispatch failed', error);
    }
  };
  const matchesHotkey = (event, target) => {
    if (!target) return false;
    if (!!target.ctrl !== !!event.ctrlKey) return false;
    if (!!target.shift !== !!event.shiftKey) return false;
    if (!!target.alt !== !!event.altKey) return false;
    if (!!target.meta !== !!event.metaKey) return false;
    const key = (target.key || '').toLowerCase();
    return !key || key === (event.key || '').toLowerCase();
  };
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const selector = buildSelector(target);
    if (!selector || target.tagName === 'HTML' || target.tagName === 'BODY') return;
    send({
      type: 'click',
      selector,
      tag: target.tagName.toLowerCase(),
      text: cleanText(target.innerText || target.textContent || ''),
      label: labelFor(target),
      timestamp: now(),
    });
  }, true);
  document.addEventListener('input', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
    const selector = buildSelector(target);
    if (!selector) return;
    const isPassword = target.type && target.type.toLowerCase() === 'password';
    send({
      type: 'fill',
      selector,
      value: isPassword ? '***' : target.value,
      inputType: target.type || target.tagName.toLowerCase(),
      placeholder: target.getAttribute('placeholder') || '',
      label: labelFor(target),
      timestamp: now(),
    });
  }, true);
  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target instanceof HTMLSelectElement) {
      const selector = buildSelector(target);
      if (!selector) return;
      const options = Array.from(target.selectedOptions || []);
      send({
        type: 'select_option',
        selector,
        value: options.map((opt) => opt.value).join(','),
        selectedLabels: options.map((opt) => cleanText(opt.textContent || '')),
        label: labelFor(target),
        timestamp: now(),
      });
    }
  }, true);
  document.addEventListener('keydown', (event) => {
    if (matchesHotkey(event, config.stopHotkey)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      send({ type: 'hotkey', action: 'stop', timestamp: now() });
      return;
    }
    if (matchesHotkey(event, config.shotHotkey)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      send({ type: 'hotkey', action: 'screenshot', timestamp: now() });
    }
  }, true);
})();
"""


def _parse_hotkey(combo: str) -> dict[str, Any]:
    """Convert a hotkey string (e.g. ``Ctrl+Shift+S``) into a flag mapping."""

    tokens = [token.strip().lower() for token in combo.split("+") if token.strip()]
    key = ""
    ctrl = shift = alt = meta = False
    for token in tokens:
        if token in {"ctrl", "control"}:
            ctrl = True
        elif token in {"shift"}:
            shift = True
        elif token in {"alt", "option"}:
            alt = True
        elif token in {"meta", "cmd", "command", "super"}:
            meta = True
        else:
            key = token
    return {"ctrl": ctrl, "shift": shift, "alt": alt, "meta": meta, "key": key}


def _build_init_script(stop_hotkey: dict[str, Any], shot_hotkey: dict[str, Any]) -> str:
    config = {"stopHotkey": stop_hotkey, "shotHotkey": shot_hotkey}
    preferred = [
        "data-testid",
        "data-test",
        "data-qa",
        "data-automation-id",
        "data-tracking-id",
        "data-role",
        "data-id",
        "data-name",
        "aria-label",
        "aria-labelledby",
        "id",
    ]
    script = _INIT_SCRIPT.replace("__CONFIG__", json.dumps(config))
    script = script.replace("__PREFERRED__", json.dumps(preferred))
    return script


class TrainerRecorder:
    """Launch Chromium in headful mode and record interactions."""

    def __init__(self, headless: bool = False) -> None:
        self.headless = headless

    async def record(
        self,
        stop_hotkey: str = "Ctrl+Shift+Q",
        shot_hotkey: str = "Ctrl+Shift+S",
    ) -> RecordingResult:
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "Playwright is required for recording. Install it with `pip install playwright` "
                "and `python -m playwright install chromium`."
            ) from exc

        stop_cfg = _parse_hotkey(stop_hotkey)
        shot_cfg = _parse_hotkey(shot_hotkey)

        temp_dir = Path(tempfile.mkdtemp(prefix="backlink-recorder-"))
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        recorded_actions: list[RecordedAction] = []
        screenshot_paths: list[Path] = []
        last_url: str | None = None
        last_click_index: int | None = None

        async def handle_binding(source: Any, event: Any) -> None:  # type: ignore[override]
            if isinstance(event, dict):
                await queue.put(event)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            await page.expose_binding("_trainerRecordEvent", handle_binding)
            await page.add_init_script(_build_init_script(stop_cfg, shot_cfg))

            loop = asyncio.get_running_loop()

            def navigation_handler(frame: Any) -> None:
                nonlocal last_url, last_click_index
                try:
                    if frame != page.main_frame:
                        return
                    url = frame.url
                except Exception:  # pragma: no cover - defensive
                    return
                if not url or url.startswith("about:blank"):
                    return
                if url == last_url:
                    return
                last_url = url
                payload = {"type": "goto", "url": url, "timestamp": time.time()}
                if last_click_index is not None and 0 <= last_click_index < len(recorded_actions):
                    meta = recorded_actions[last_click_index].meta or {}
                    meta["navigated"] = True
                    recorded_actions[last_click_index].meta = meta
                loop.create_task(queue.put(payload))

            page.on("framenavigated", navigation_handler)

            await page.bring_to_front()

            stop = False
            screenshot_counter = 1

            while not stop:
                event = await queue.get()
                event_type = event.get("type")
                timestamp = float(event.get("timestamp")) if event.get("timestamp") else time.time()

                if event_type == "hotkey":
                    action = event.get("action")
                    if action == "stop":
                        stop = True
                        continue
                    if action == "screenshot":
                        filename = f"screenshot-{screenshot_counter:03d}.png"
                        screenshot_counter += 1
                        target_path = temp_dir / filename
                        try:
                            await page.screenshot(path=str(target_path), full_page=True)
                        except Exception as exc:  # pragma: no cover - runtime guard
                            logger.warning("Failed to capture screenshot: %s", exc)
                            continue
                        screenshot_paths.append(target_path)
                        recorded_actions.append(
                            RecordedAction(
                                type="screenshot",
                                value=filename,
                                meta={"timestamp": timestamp, "temp_path": str(target_path)},
                            )
                        )
                        continue

                if event_type == "goto":
                    url = event.get("url")
                    if not url:
                        continue
                    recorded_actions.append(
                        RecordedAction(
                            type="goto",
                            url=url,
                            value=url,
                            meta={"timestamp": timestamp},
                        )
                    )
                    last_click_index = None
                    continue

                if event_type == "click":
                    selector = event.get("selector")
                    if not selector:
                        continue
                    meta = {
                        "timestamp": timestamp,
                        "tag": event.get("tag"),
                        "text": event.get("text"),
                        "label": event.get("label"),
                    }
                    recorded_actions.append(RecordedAction(type="click", selector=selector, meta=meta))
                    last_click_index = len(recorded_actions) - 1
                    continue

                if event_type == "fill":
                    selector = event.get("selector")
                    if not selector:
                        continue
                    value = event.get("value")
                    meta = {
                        "timestamp": timestamp,
                        "input_type": event.get("inputType"),
                        "placeholder": event.get("placeholder"),
                        "label": event.get("label"),
                    }
                    recorded_actions.append(
                        RecordedAction(type="fill", selector=selector, value=value, meta=meta)
                    )
                    last_click_index = None
                    continue

                if event_type == "select_option":
                    selector = event.get("selector")
                    if not selector:
                        continue
                    value = event.get("value")
                    meta = {
                        "timestamp": timestamp,
                        "labels": event.get("selectedLabels"),
                        "label": event.get("label"),
                    }
                    recorded_actions.append(
                        RecordedAction(type="select_option", selector=selector, value=value, meta=meta)
                    )
                    last_click_index = None
                    continue

            await browser.close()

        if not screenshot_paths:
            shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Recording finished with %s actions", len(recorded_actions))
        return RecordingResult(actions=recorded_actions, screenshots=screenshot_paths)


__all__ = ["TrainerRecorder", "RecordedAction", "RecordingResult"]

