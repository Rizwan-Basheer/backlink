from __future__ import annotations

from backlink.services.ai import (
    generate_blog_post,
    generate_profile_assets,
    summarize_and_keywords,
)


def test_generate_blog_post_includes_url(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    meta = {
        "url": "https://example.com/post",
        "title": "How to Paint",
        "summary": "A guide to painting walls.",
        "keywords": "paint, walls",
    }
    post = generate_blog_post(meta, min_words=80, include_headings=False)
    assert "https://example.com/post" in post
    assert len(post.split()) >= 80


def test_generate_profile_assets_contains_keywords(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    meta = {
        "url": "https://example.com",
        "title": "Example",
        "keywords": "marketing, growth",
    }
    assets = generate_profile_assets(meta, min_bio_words=30, min_caption_words=10)
    assert "https://example.com" in assets["bio"]
    assert len(assets["bio"].split()) >= 30
    assert len(assets["caption"].split()) >= 10


def test_summarize_and_keywords_basic():
    html = "<html><head><title>Test</title></head><body><p>Painting tips for beginners.</p></body></html>"
    data = summarize_and_keywords(html)
    assert "summary" in data
    assert "keywords" in data
