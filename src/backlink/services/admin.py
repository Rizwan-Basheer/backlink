"""Administrative helpers for targets, AI content, and troubleshooting."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

try:  # pragma: no cover - optional dependency
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore
import urllib.request
from sqlmodel import Session, select

from ..config import CONTENT_CACHE_DAYS, TARGET_DIR
from ..models import GeneratedAsset, Recipe, TargetURL
from ..utils.files import write_text
from ..utils.strings import join_non_empty, slugify
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
        self._validate_url(url)
        existing = self.session.exec(select(TargetURL).where(TargetURL.url == url)).first()
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
            url=url,
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

        snapshot_path = self._snapshot_path(target_obj.url)
        write_text(snapshot_path, html)
        metadata = self._extract_metadata(html)

        target_obj.title = target_obj.title or metadata.get("title")
        target_obj.description = target_obj.description or metadata.get("description")
        target_obj.keywords = target_obj.keywords or metadata.get("keywords")

        if not target_obj.summary or not target_obj.keywords:
            ai_meta = summarize_and_keywords(html)
            target_obj.summary = target_obj.summary or ai_meta.get("summary")
            target_obj.keywords = target_obj.keywords or ai_meta.get("keywords")

        target_obj.html_snapshot_path = str(snapshot_path)
        target_obj.updated_at = datetime.utcnow()
        self.session.add(target_obj)
        self.session.flush()
        return target_obj

    # Content generation ------------------------------------------------
    def generate_content_for_target(
        self,
        target: TargetURL | int,
        *,
        recipe: Recipe,
        style_hints: Mapping[str, Any] | None = None,
        refresh: bool = False,
    ) -> dict[str, str]:
        target_obj = self.get_target(target)
        style_hints = style_hints or {}
        recipe_obj = self.session.get(Recipe, recipe.id) if not recipe.category else recipe
        category_key = self._normalise_category(recipe_obj.category.name if recipe_obj and recipe_obj.category else "")
        metadata = {
            "url": target_obj.url,
            "title": target_obj.title,
            "description": target_obj.description,
            "summary": target_obj.summary,
            "keywords": target_obj.keywords,
        }

        if category_key == "profile_backlinks":
            requirements = style_hints.get("profile_backlinks", {})
            assets = self._get_cached_assets(
                target_obj,
                ("profile_bio", "profile_caption", "profile_short_description"),
                refresh=refresh,
            )
            missing = {key: val for key, val in assets.items() if val}
            if len(missing) == 3:
                return {
                    "GENERATED_BIO": missing["profile_bio"],
                    "GENERATED_CAPTION": missing["profile_caption"],
                    "GENERATED_DESCRIPTION": missing["profile_short_description"],
                }
            generated = generate_profile_assets(
                metadata,
                tone=requirements.get("tone", "professional"),
                min_bio_words=int(requirements.get("min_bio_words", 60)),
                min_caption_words=int(requirements.get("min_caption_words", 20)),
            )

            self._store_assets(
                target_obj,
                recipe_obj or recipe,
                {
                    "profile_bio": generated.get("bio", ""),
                    "profile_caption": generated.get("caption", ""),
                    "profile_short_description": generated.get("short_description", ""),
                },
            )
            return {
                "GENERATED_BIO": generated.get("bio", ""),
                "GENERATED_CAPTION": generated.get("caption", ""),
                "GENERATED_DESCRIPTION": generated.get("short_description", ""),
                "GENERATED_SHORT_DESCRIPTION": generated.get("short_description", ""),
            }

        if category_key == "blog_backlinks":
            requirements = style_hints.get("blog_backlinks", {})
            cached = self._get_cached_assets(
                target_obj,
                ("blog_post",),
                refresh=refresh,
            )
            if cached.get("blog_post"):
                return {"GENERATED_BLOG": cached["blog_post"]}
            blog_post = generate_blog_post(
                metadata,
                min_words=int(requirements.get("min_words", 400)),
                include_headings=bool(requirements.get("headings", True)),
                tone=requirements.get("tone", "helpful"),
            )
            self._store_assets(target_obj, recipe_obj or recipe, {"blog_post": blog_post})
            return {"GENERATED_BLOG": blog_post}

        logger.info("No AI content required for category '%s'", category_key)
        return {}

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
    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("target URL must start with http:// or https://")
        if not parsed.netloc:
            raise ValueError("target URL is missing a hostname")

    def _fetch_url(self, url: str) -> str:
        if httpx is not None:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        with urllib.request.urlopen(url, timeout=30.0) as response:  # type: ignore[arg-type]
            data = response.read()
            return data.decode("utf-8", errors="ignore")

    def _snapshot_path(self, url: str) -> Path:
        parsed = urlparse(url)
        slug = slugify(join_non_empty([parsed.netloc, parsed.path], sep="-")) or "target"
        return TARGET_DIR / f"{slug}.html"

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
        kinds: tuple[str, ...],
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
        recipe: Recipe,
        payload: Mapping[str, str],
    ) -> None:
        timestamp = datetime.utcnow()
        for kind, content in payload.items():
            if not content:
                continue
            asset = GeneratedAsset(
                target_id=target.id,
                recipe_id=recipe.id,
                kind=kind,
                content=content,
                created_at=timestamp,
            )
            self.session.add(asset)
        self.session.flush()


__all__ = ["AdminService"]

