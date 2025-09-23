"""Lightweight helpers for OpenAI-powered content generation and healing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

try:  # pragma: no cover - optional dependency
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore

from ..utils.strings import join_non_empty

logger = logging.getLogger(__name__)


class AIIntegrationError(RuntimeError):
    """Raised when the OpenAI integration is unavailable."""


@dataclass
class _TokenBucket:
    capacity: int
    refill_time: float
    tokens: float = 0.0
    updated_at: float = time.monotonic()

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)

    def consume(self, amount: float = 1.0) -> None:
        now = time.monotonic()
        elapsed = now - self.updated_at
        refill_rate = self.capacity / self.refill_time
        self.tokens = min(self.capacity, self.tokens + elapsed * refill_rate)
        self.updated_at = now
        if self.tokens < amount:
            # Simple sleep to respect the limit without busy waiting.
            wait_time = (amount - self.tokens) / refill_rate
            logger.debug("AI rate limiter sleeping for %.2fs", wait_time)
            time.sleep(max(wait_time, 0))
            self.tokens = min(self.capacity, self.tokens + wait_time * refill_rate)
            self.updated_at = time.monotonic()
        self.tokens -= amount


_RATE_LIMIT = _TokenBucket(capacity=int(os.getenv("BACKLINK_OPENAI_RATE", "60")), refill_time=60.0)
_DEFAULT_MODEL = os.getenv("BACKLINK_OPENAI_MODEL", "gpt-4o-mini")
_TIMEOUT = float(os.getenv("BACKLINK_OPENAI_TIMEOUT", "45"))


def _log_prompt_hash(messages: list[dict[str, str]], model: str) -> None:
    prompt = "\n".join(f"{item['role']}: {item['content']}" for item in messages)
    prompt_hash = hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    logger.info("Dispatching OpenAI request model=%s prompt_hash=%s", model, prompt_hash)


def _call_openai(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.6,
    max_tokens: int = 800,
    response_format: Mapping[str, Any] | None = None,
) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise AIIntegrationError("OPENAI_API_KEY is not configured")

    _RATE_LIMIT.consume()
    model = _DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {api_key}"}
    payload: MutableMapping[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    _log_prompt_hash(messages, model)
    if httpx is None:  # pragma: no cover - optional dependency missing
        raise AIIntegrationError("httpx is required for OpenAI integration")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
        response.raise_for_status()
    except Exception as exc:  # pragma: no cover - network heavy
        raise AIIntegrationError(str(exc)) from exc

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
        raise AIIntegrationError("unexpected OpenAI response structure") from exc


def _keywords_from_text(text: str, *, limit: int = 12) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", text.lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords


def summarize_and_keywords(html_text: str) -> dict[str, str]:
    """Summarise the provided HTML text and extract keywords.

    The function attempts to use the OpenAI API but falls back to a deterministic
    heuristic implementation when the integration is not available.
    """

    text = re.sub(r"\s+", " ", _strip_tags(html_text)).strip()
    if not text:
        return {"summary": "", "keywords": ""}

    messages = [
        {
            "role": "system",
            "content": (
                "You summarise webpages for backlink creation. "
                "Return concise SEO-friendly JSON without code fences."
            ),
        },
        {
            "role": "user",
            "content": (
                "Summarise the following page in 3 sentences and provide 8 keywords. "
                "Return JSON with keys summary and keywords. Keywords should be a "
                "comma separated list. Page text: "
                f"{text[:5000]}"
            ),
        },
    ]
    try:
        response = _call_openai(messages, response_format={"type": "json_object"}, max_tokens=400)
        data = json.loads(response)
        summary = str(data.get("summary", "")).strip()
        keywords = str(data.get("keywords", "")).strip()
        return {"summary": summary, "keywords": keywords}
    except (AIIntegrationError, json.JSONDecodeError):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        summary = " ".join(sentences[:3]).strip()
        if not summary:
            summary = text[:280].strip()
        keywords = ", ".join(_keywords_from_text(text))
        return {"summary": summary, "keywords": keywords}


def generate_profile_assets(
    target_meta: Mapping[str, Any],
    *,
    tone: str = "professional",
    min_bio_words: int = 60,
    min_caption_words: int = 20,
) -> dict[str, str]:
    """Generate bio/caption/description assets tailored to the target."""

    base_prompt = (
        "Create concise, HTML-safe profile materials referencing the backlink "
        "target. Avoid markdown or code fences."
    )
    metadata = _format_target_metadata(target_meta)
    messages = [
        {"role": "system", "content": base_prompt},
        {
            "role": "user",
            "content": (
                "Provide JSON with keys bio, caption, short_description. "
                f"Bio must be at least {min_bio_words} words, caption at least "
                f"{min_caption_words} words, and both must weave in the target "
                "URL and keywords naturally. Maintain a {tone} tone. "
                f"Metadata: {metadata}"
            ),
        },
    ]
    try:
        response = _call_openai(messages, response_format={"type": "json_object"}, max_tokens=600)
        data = json.loads(response)
        return {
            "bio": str(data.get("bio", "")).strip(),
            "caption": str(data.get("caption", "")).strip(),
            "short_description": str(data.get("short_description", "")).strip(),
        }
    except (AIIntegrationError, json.JSONDecodeError):
        return _fallback_profile_assets(target_meta, tone=tone, min_bio_words=min_bio_words, min_caption_words=min_caption_words)


def generate_blog_post(
    target_meta: Mapping[str, Any],
    *,
    min_words: int = 400,
    include_headings: bool = True,
    tone: str = "helpful",
) -> str:
    """Generate a blog post style snippet linking back to the target URL."""

    metadata = _format_target_metadata(target_meta)
    headings_instruction = "Include descriptive H2 headings." if include_headings else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You write SEO-focused guest posts that include the provided "
                "target URL naturally. Avoid markdown fences and keep HTML safe."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Write at least {min_words} words in {tone} tone. "
                "The post must include the target URL verbatim once in the body, "
                "use short paragraphs, and end with a call to action. "
                f"{headings_instruction} Metadata: {metadata}"
            ),
        },
    ]
    try:
        return _call_openai(messages, max_tokens=1200)
    except AIIntegrationError:
        return _fallback_blog_post(target_meta, min_words=min_words, include_headings=include_headings, tone=tone)


def troubleshoot_playwright(
    error_log: str,
    last_action: Mapping[str, Any],
    dom_snippet: str,
    *,
    page_url: str | None = None,
) -> dict[str, Any]:
    """Ask the LLM for updated selectors or guidance when a step fails."""

    messages = [
        {
            "role": "system",
            "content": (
                "You are assisting with Playwright automation fixes. Provide JSON "
                "with keys selector (optional) and notes explaining the fix."
            ),
        },
        {
            "role": "user",
            "content": (
                "A Playwright step failed. Suggest a better CSS selector or next "
                "action. Return JSON."
                f" Error: {error_log}. Action: {json.dumps(last_action)}. "
                f"DOM snippet: {dom_snippet[:2000]}. Page: {page_url or 'unknown'}."
            ),
        },
    ]
    try:
        response = _call_openai(messages, response_format={"type": "json_object"}, max_tokens=400)
        data = json.loads(response)
        return {
            "selector": data.get("selector"),
            "notes": data.get("notes"),
        }
    except (AIIntegrationError, json.JSONDecodeError):
        return _heuristic_selector(dom_snippet, last_action)


def _format_target_metadata(target_meta: Mapping[str, Any]) -> str:
    parts = [
        f"URL: {target_meta.get('url', '')}",
        f"Title: {target_meta.get('title', '')}",
        f"Description: {target_meta.get('description', '')}",
        f"Summary: {target_meta.get('summary', '')}",
        f"Keywords: {target_meta.get('keywords', '')}",
    ]
    return " | ".join(parts)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _fallback_profile_assets(
    target_meta: Mapping[str, Any],
    *,
    tone: str,
    min_bio_words: int,
    min_caption_words: int,
) -> dict[str, str]:
    keywords = str(target_meta.get("keywords") or "").split(",")
    keywords = [kw.strip() for kw in keywords if kw.strip()]
    base_topic = target_meta.get("title") or target_meta.get("summary") or "the featured resource"
    url = target_meta.get("url", "")
    bio = (
        f"{base_topic} is highlighted at {url}. We share insights and guidance "
        "that help audiences take action right away."
    )
    if keywords:
        bio += " Key focuses include " + ", ".join(keywords[:4]) + "."
    caption = (
        f"Discover more about {base_topic.lower()} at {url}. Tap through for "
        "actionable takeaways today."
    )
    short_description = join_non_empty(
        [
            f"Learn about {base_topic.lower()} in moments.",
            f"Visit {url} to explore the full story.",
        ],
        sep=" ",
    )
    bio = _ensure_word_count(bio, min_bio_words)
    caption = _ensure_word_count(caption, min_caption_words)
    return {"bio": bio, "caption": caption, "short_description": short_description}


def _fallback_blog_post(
    target_meta: Mapping[str, Any],
    *,
    min_words: int,
    include_headings: bool,
    tone: str,
) -> str:
    url = target_meta.get("url", "")
    title = target_meta.get("title") or "this resource"
    summary = target_meta.get("summary") or target_meta.get("description") or ""
    keywords = str(target_meta.get("keywords") or "").split(",")
    keywords = [kw.strip() for kw in keywords if kw.strip()]

    intro = f"Exploring {title} can unlock new perspectives for your next project."
    if summary:
        intro += f" {summary.strip()}"
    body = [intro]
    if include_headings:
        body.append(f"\nH2: Why {title} matters\n")
    body.append(
        "This guide distils the most valuable lessons from the featured page "
        "so you can apply them immediately."
    )
    if keywords:
        body.append("Key topics include " + ", ".join(keywords[:5]) + ".")
    body.append(
        f"Read the complete insights at {url} to dive deeper into the strategies "
        "that resonate with your goals."
    )
    body.append("Take the next step today and put these ideas into practice.")
    text = "\n\n".join(body)
    return _ensure_word_count(text, min_words)


def _ensure_word_count(text: str, minimum: int) -> str:
    words = text.split()
    if len(words) >= minimum:
        return text
    filler = (
        " These insights provide practical steps, relevant examples, "
        "and a clear reason to click through for more."
    )
    while len(words) < minimum:
        words.extend(filler.split())
    return " ".join(words)


def _heuristic_selector(dom_snippet: str, action: Mapping[str, Any]) -> dict[str, Any]:
    if not dom_snippet:
        return {}
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        return {}

    soup = BeautifulSoup(dom_snippet, "html.parser")
    target_selector = action.get("selector")
    # Prefer id-based selectors.
    candidate = soup.find(attrs={"id": True})
    if candidate:
        return {
            "selector": f"#{candidate['id']}",
            "notes": "Heuristic id-based selector suggested after failure.",
        }
    candidate = soup.find(attrs={"data-test": True})
    if candidate:
        value = candidate.get("data-test")
        return {
            "selector": f"[data-test='{value}']",
            "notes": "Heuristic data-test selector suggested after failure.",
        }
    candidate = soup.find(attrs={"name": True})
    if candidate:
        return {
            "selector": f"{candidate.name}[name='{candidate['name']}']",
            "notes": "Heuristic name selector suggested after failure.",
        }
    if target_selector and isinstance(target_selector, str) and target_selector.startswith("#"):
        return {
            "selector": target_selector.replace("#", ".", 1),
            "notes": "Fallback heuristic swapped id selector to class search.",
        }
    return {}


__all__ = [
    "AIIntegrationError",
    "generate_profile_assets",
    "generate_blog_post",
    "summarize_and_keywords",
    "troubleshoot_playwright",
]

