"""Administrative helpers for targets, AI content, and troubleshooting."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

try:  # pragma: no cover - optional dependency
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore
import urllib.request
from sqlmodel import Session, select

from ..config import CONTENT_CACHE_DAYS, DEFAULT_TIMEOUT_MS, SNAPSHOT_DIR
from ..models import GeneratedAsset, Recipe, TargetURL
from ..utils.files import write_text
from .ai import (
    AIIntegrationError,
    generate_blog_post,
    generate_profile_assets,
    summarize_and_keywords,
    troubleshoot_playwright,
)

logger = logging.getLogger(__name__)


class AdminService:
    """Facade aggregating admin-centric workflows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # Target management -------------------------------------------------
    def register_target_url(
        self,
        url: str,
        *,
        title: str | None = None,
        description: str | None = None,
        keywords: str | None = None,
        summary: str | None = None,
    ) -> TargetURL:
        url_value = str(url)
        self._validate_url(url_value)
        existing = self.session.exec(select(TargetURL).where(TargetURL.url == url_value)).first()
        timestamp = datetime.utcnow()
        if existing:
            if title:
                existing.title = title
            if description:
                existing.description = description
            if keywords:
                existing.keywords = keywords
            if summary:
                existing.summary = summary
            existing.updated_at = timestamp
            self.session.add(existing)
            self.session.flush()
            return existing

        target = TargetURL(
            url=url_value,
            title=title,
            description=description,
            keywords=keywords,
            summary=summary,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.session.add(target)
        self.session.flush()
        return target

    def list_targets(self, search: str | None = None) -> list[TargetURL]:
        statement = select(TargetURL).order_by(TargetURL.created_at.desc())
        if search:
            statement = statement.where(TargetURL.url.contains(search))
        return self.session.exec(statement).all()

    def get_target(self, target: TargetURL | int) -> TargetURL:
        if isinstance(target, TargetURL):
            return target
        result = self.session.get(TargetURL, target)
        if not result:
            raise ValueError(f"target {target} not found")
        return result

    def fetch_and_enrich_target(self, target: TargetURL | int) -> TargetURL:
        target_obj = self.get_target(target)
        logger.info("Fetching metadata for %s", target_obj.url)
        try:
            html = self._fetch_url(target_obj.url)
        except Exception as exc:  # pragma: no cover - network
            logger.warning("Unable to fetch %s: %s", target_obj.url, exc)
            return target_obj

        if target_obj.id is None:
            self.session.add(target_obj)
            self.session.flush()

        snapshot_path = self._snapshot_path(target_obj)
        write_text(snapshot_path, html)
        metadata = self._extract_metadata(html)

        target_obj.title = target_obj.title or metadata.get("title") or ""
        target_obj.description = target_obj.description or metadata.get("description") or ""
        target_obj.keywords = target_obj.keywords or metadata.get("keywords") or ""

        ai_meta: dict[str, str] | None = None
        if not target_obj.summary or not target_obj.keywords:
            ai_meta = summarize_and_keywords(html, url=target_obj.url)
            target_obj.summary = target_obj.summary or ai_meta.get("summary", "")
            target_obj.keywords = target_obj.keywords or ai_meta.get("keywords", "")

        target_obj.html_snapshot_path = str(snapshot_path)
        target_obj.updated_at = datetime.utcnow()
        self.session.add(target_obj)
        self.session.flush()

        if ai_meta:
            self._store_assets(target_obj, {"summary": target_obj.summary or "", "keywords": target_obj.keywords or ""})
        return target_obj

    # Content generation ------------------------------------------------
    def generate_content_for_target(
        self,
        target: TargetURL | int,
        *,
        recipe: Recipe | None = None,
        category_name: str | None = None,
        style_hints: Mapping[str, Any] | None = None,
        kinds: Sequence[str] | None = None,
        refresh: bool = False,
    ) -> dict[str, str]:
        target_obj = self.get_target(target)
        style_hints = style_hints or {}
        recipe_obj = recipe
        if recipe_obj and not recipe_obj.category and recipe_obj.id:
            recipe_obj = self.session.get(Recipe, recipe_obj.id)

        category_key = self._normalise_category(
            category_name
            or (recipe_obj.category.name if recipe_obj and recipe_obj.category else "")
        )

        requested = set(kinds or [])
        include_summary = "summary" in requested
        include_keywords = "keywords" in requested
        needs_profile = bool(requested & {"profile_bio", "caption"}) or (
            not requested and category_key == "profile_backlinks"
        )
        needs_blog = ("blog_post" in requested) or (
            not requested and category_key == "blog_backlinks"
        )

        if include_summary or include_keywords or not target_obj.summary or not target_obj.keywords:
            target_obj = self.fetch_and_enrich_target(target_obj)

        result: dict[str, str] = {}
        metadata = {
            "url": target_obj.url,
            "title": target_obj.title,
            "description": target_obj.description,
            "summary": target_obj.summary,
            "keywords": target_obj.keywords,
        }

        if include_summary:
            result["SUMMARY"] = target_obj.summary or ""
        if include_keywords:
            result["KEYWORDS"] = target_obj.keywords or ""

        if needs_profile:
            profile_requirements = style_hints.get("profile_backlinks", {})
            requested_profile = {"profile_bio", "caption"}
            if requested:
                requested_profile &= requested
            if not requested_profile:
                requested_profile = {"profile_bio", "caption"}
            cached = {}
            if not refresh:
                cached = self._get_cached_assets(target_obj, tuple(requested_profile))
            cache_hits = {k: v for k, v in cached.items() if v}
            if requested_profile <= set(cache_hits.keys()):
                bio = cache_hits.get("profile_bio", "")
                caption = cache_hits.get("caption", "")
                result.update(self._profile_payload(bio, caption))
            else:
                generated = generate_profile_assets(
                    metadata,
                    tone=profile_requirements.get("tone", "friendly"),
                    min_bio_words=int(profile_requirements.get("min_bio_words", 80)),
                    min_caption_words=int(profile_requirements.get("min_caption_words", 12)),
                    max_bio_words=int(profile_requirements.get("max_bio_words", 120)),
                    max_caption_words=int(profile_requirements.get("max_caption_words", 30)),
                )
                bio = generated.get("bio", "")
                caption = generated.get("caption", "")
                short_description = generated.get("short_description", "")
                self._store_assets(
                    target_obj,
                    {"profile_bio": bio, "caption": caption},
                    recipe=recipe_obj,
                )
                result.update(self._profile_payload(bio, caption, short_description))

        if needs_blog:
            blog_requirements = style_hints.get("blog_backlinks", {})
            cached = {} if refresh else self._get_cached_assets(target_obj, ("blog_post",))
            if cached.get("blog_post"):
                result.update(self._blog_payload(cached["blog_post"]))
            else:
                blog_post = generate_blog_post(
                    metadata,
                    min_words=int(blog_requirements.get("min_words", 600)),
                    max_words=int(blog_requirements.get("max_words", 900)),
                    include_headings=bool(blog_requirements.get("headings", True)),
                    tone=blog_requirements.get("tone", "helpful"),
                )
                self._store_assets(target_obj, {"blog_post": blog_post}, recipe=recipe_obj)
                result.update(self._blog_payload(blog_post))

        return result

    def resolve_runtime_variables(
        self,
        target: TargetURL,
        generated_payload: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        generated_payload = generated_payload or {}
        variables = {
            "TARGET_URL": target.url,
            "TARGET_TITLE": target.title or "",
            "TARGET_DESCRIPTION": target.description or "",
            "TARGET_SUMMARY": target.summary or "",
            "TARGET_KEYWORDS": target.keywords or "",
            "TARGET_KEYWORDS_LIST": [
                kw.strip()
                for kw in (target.keywords or "").split(",")
                if kw.strip()
            ],
        }
        for key, value in generated_payload.items():
            variables[key] = value
        return variables

    # Troubleshooting ---------------------------------------------------
    def llm_troubleshoot(self, context: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return troubleshoot_playwright(
                context.get("error", ""),
                context.get("action", {}),
                context.get("dom", ""),
                page_url=context.get("url"),
            )
        except AIIntegrationError as exc:
            logger.warning("Troubleshooter unavailable: %s", exc)
            return {}

    # Internal helpers --------------------------------------------------
    def _validate_url(self, url: Any) -> None:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("target URL must start with http:// or https://")
        if not parsed.netloc:
            raise ValueError("target URL is missing a hostname")

    def _fetch_url(self, url: str) -> str:
        last_error: Exception | None = None
        if httpx is not None:
            try:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.text
            except Exception as exc:  # pragma: no cover - network
                last_error = exc
                logger.debug("Primary fetch failed for %s: %s", url, exc)
        try:
            return self._fetch_with_playwright(url)
        except Exception as exc:  # pragma: no cover - optional dependency
            if not last_error:
                last_error = exc
            logger.debug("Playwright fallback failed for %s: %s", url, exc)
        with urllib.request.urlopen(url, timeout=30.0) as response:  # type: ignore[arg-type]
            data = response.read()
            return data.decode("utf-8", errors="ignore")

    def _fetch_with_playwright(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("playwright is not installed") from exc

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=DEFAULT_TIMEOUT_MS)
                html = page.content()
            finally:
                browser.close()
        return html

    def _snapshot_path(self, target: TargetURL) -> Path:
        identifier = target.id or 0
        return SNAPSHOT_DIR / f"{identifier}.html"

    def _extract_metadata(self, html: str) -> dict[str, str]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:  # pragma: no cover - optional dependency
            return {}
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        description = ""
        desc_tag = soup.find("meta", attrs={"name": "description"})
        if desc_tag and desc_tag.get("content"):
            description = desc_tag.get("content", "").strip()
        keywords = ""
        keywords_tag = soup.find("meta", attrs={"name": "keywords"})
        if keywords_tag and keywords_tag.get("content"):
            keywords = keywords_tag.get("content", "").strip()
        text = soup.get_text(" ", strip=True)
        return {"title": title, "description": description, "keywords": keywords, "text": text}

    def _profile_payload(
        self,
        bio: str,
        caption: str,
        short_description: str | None = None,
    ) -> dict[str, str]:
        payload: dict[str, str] = {}
        if bio:
            payload["PROFILE_BIO"] = bio
            payload["GENERATED_BIO"] = bio
        if caption:
            payload["CAPTION"] = caption
            payload["PROFILE_CAPTION"] = caption
            payload["GENERATED_CAPTION"] = caption
        if short_description:
            payload["PROFILE_SUMMARY"] = short_description
            payload["GENERATED_DESCRIPTION"] = short_description
        return payload

    def _blog_payload(self, blog_post: str) -> dict[str, str]:
        if not blog_post:
            return {}
        return {"BLOG_POST": blog_post, "GENERATED_BLOG": blog_post}

    def _normalise_category(self, category_name: str) -> str:
        name = (category_name or "").lower().strip()
        if "profile" in name:
            return "profile_backlinks"
        if "blog" in name:
            return "blog_backlinks"
        return name.replace(" ", "_")

    def _get_cached_assets(
        self,
        target: TargetURL,
        kinds: Sequence[str],
        *,
        refresh: bool,
    ) -> dict[str, str]:
        if refresh:
            return {}
        cutoff = datetime.utcnow() - timedelta(days=CONTENT_CACHE_DAYS)
        statement = (
            select(GeneratedAsset)
            .where(GeneratedAsset.target_id == target.id)
            .where(GeneratedAsset.kind.in_(kinds))
            .where(GeneratedAsset.created_at >= cutoff)
        )
        assets = self.session.exec(statement).all()
        result = {asset.kind: asset.content for asset in assets if asset.content}
        if result:
            logger.info(
                "Reusing cached assets for target %s (%s)",
                target.url,
                ", ".join(result.keys()),
            )
        return result

    def _store_assets(
        self,
        target: TargetURL,
        payload: Mapping[str, str],
        *,
        recipe: Recipe | None = None,
    ) -> None:
        timestamp = datetime.utcnow()
        for kind, content in payload.items():
            if not content:
                continue
            asset = GeneratedAsset(
                target_id=target.id,
                recipe_id=recipe.id if recipe else None,
                kind=kind,
                content=content,
                created_at=timestamp,
            )
            self.session.add(asset)
        self.session.flush()


__all__ = ["AdminService"]

