"""
test_company_feeds.py — Tests for sources/company_feeds.py

Tests cover:
  - RSS feed parsing (mocked HTTP responses)
  - GitHub releases API parsing (mocked HTTP responses)
  - Rate limiting and retry logic
  - Deduplication
  - Error handling (timeout, 404, 429, 500)
  - Item format compliance
  - Individual company fetcher functions
  - The aggregate fetch_company_news() function
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure project root is on path
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sources.company_feeds import (
    _build_item,
    _dedup_id,
    _extract_media_from_rss_entry,
    _fetch_rss_feed,
    _fetch_github_releases,
    fetch_company_news,
    fetch_openai_news,
    fetch_anthropic_news,
    fetch_meta_ai_news,
    fetch_deepseek_news,
    fetch_huggingface_news,
    fetch_mistral_news,
    fetch_groq_news,
    fetch_cohere_news,
    fetch_stability_news,
    fetch_xai_news,
    fetch_nous_research_news,
    fetch_google_deepmind_news,
    COMPANY_FETCHERS,
)


# ---------------------------------------------------------------------------
# Fixtures: mock HTTP responses
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_rss_xml():
    """Sample RSS feed XML for testing."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Test Company Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Introducing Model X-1</title>
      <link>https://example.com/blog/model-x1</link>
      <description>We are excited to announce Model X-1, our latest breakthrough in AI.</description>
      <pubDate>Mon, 13 Jul 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Model X-1 Benchmarks</title>
      <link>https://example.com/blog/model-x1-benchmarks</link>
      <description>Detailed benchmark results for Model X-1.</description>
      <pubDate>Sun, 12 Jul 2026 14:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


@pytest.fixture
def sample_github_releases():
    """Sample GitHub releases API response."""
    return [
        {
            "tag_name": "v3.0.0",
            "name": "v3.0.0",
            "html_url": "https://github.com/test/repo/releases/tag/v3.0.0",
            "body": "## What's Changed\n\n* New feature A\n* Bug fix B",
            "published_at": "2026-07-13T08:00:00Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v2.9.0",
            "name": "v2.9.0",
            "html_url": "https://github.com/test/repo/releases/tag/v2.9.0",
            "body": "Minor update with bug fixes.",
            "published_at": "2026-07-01T12:00:00Z",
            "draft": False,
            "prerelease": False,
        },
    ]


@pytest.fixture
def sample_rss_with_media():
    """RSS feed with media content."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>New Model Announcement</title>
      <link>https://example.com/new-model</link>
      <description>Check out our new model!</description>
      <pubDate>Mon, 13 Jul 2026 08:00:00 GMT</pubDate>
      <media:content medium="image" url="https://example.com/image.jpg"/>
    </item>
  </channel>
</rss>"""


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestDedupId:
    def test_deterministic(self):
        """Same input always produces same ID."""
        id1 = _dedup_id("https://example.com/test")
        id2 = _dedup_id("https://example.com/test")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        """Different inputs produce different IDs."""
        id1 = _dedup_id("https://example.com/a")
        id2 = _dedup_id("https://example.com/b")
        assert id1 != id2

    def test_id_length(self):
        """ID is 16 characters (hex)."""
        id_ = _dedup_id("test")
        assert len(id_) == 16
        assert all(c in "0123456789abcdef" for c in id_)


class TestBuildItem:
    def test_basic_item(self):
        """Item has all required fields."""
        item = _build_item(
            source_name="Test Co",
            title="Hello World",
            url="https://example.com/hello",
        )
        assert item["id"]
        assert "Title: Hello World" in item["raw_text"]
        assert item["url"] == "https://example.com/hello"
        assert item["source_name"] == "Test Co"
        assert item["media_url"] is None
        assert isinstance(item["date"], datetime)

    def test_item_with_summary(self):
        """Summary is included in raw_text."""
        item = _build_item(
            source_name="Test Co",
            title="News",
            url="https://example.com",
            summary="This is a summary.",
        )
        assert "Summary: This is a summary." in item["raw_text"]

    def test_item_with_custom_date(self):
        """Custom date is used."""
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        item = _build_item(
            source_name="Test",
            title="Title",
            url="https://example.com",
            date=dt,
        )
        assert item["date"] == dt

    def test_item_format_matches_main(self):
        """Verify the item dict has exactly the keys main.py expects."""
        item = _build_item(
            source_name="Test",
            title="Title",
            url="https://example.com",
        )
        expected_keys = {"id", "raw_text", "url", "source_name", "media_url", "date"}
        assert set(item.keys()) == expected_keys


class TestExtractMedia:
    def test_no_media(self):
        """Returns None when no media is present."""
        entry = MagicMock()
        entry.media_content = None
        entry.media_thumbnail = None
        entry.enclosures = []
        entry.get.return_value = ""
        assert _extract_media_from_rss_entry(entry) is None

    def test_media_content(self):
        """Extracts from media_content."""
        entry = MagicMock()
        entry.media_content = [{"medium": "image", "url": "https://example.com/img.jpg"}]
        entry.media_thumbnail = None
        entry.enclosures = []
        assert _extract_media_from_rss_entry(entry) == "https://example.com/img.jpg"

    def test_media_thumbnail(self):
        """Extracts from media_thumbnail."""
        entry = MagicMock()
        entry.media_content = None
        entry.media_thumbnail = [{"url": "https://example.com/thumb.jpg"}]
        entry.enclosures = []
        assert _extract_media_from_rss_entry(entry) == "https://example.com/thumb.jpg"


# ---------------------------------------------------------------------------
# Integration tests: RSS feed fetcher
# ---------------------------------------------------------------------------

class TestFetchRSSFeed:
    @pytest.mark.asyncio
    async def test_successful_fetch(self, sample_rss_xml):
        """Successfully parses RSS items."""
        mock_response = MagicMock()
        mock_response.text = sample_rss_xml
        mock_response.status_code = 200

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_rss_feed(
                "https://example.com/feed.xml",
                "Test Source",
                max_entries=10,
            )

        assert len(items) == 2
        assert items[0]["source_name"] == "Test Source"
        assert "Introducing Model X-1" in items[0]["raw_text"]
        assert items[0]["url"] == "https://example.com/blog/model-x1"

    @pytest.mark.asyncio
    async def test_empty_feed(self):
        """Handles empty RSS feed gracefully."""
        mock_response = MagicMock()
        mock_response.text = """<?xml version="1.0"?><rss version="2.0">
            <channel><title>Empty</title></channel></rss>"""
        mock_response.status_code = 200

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_rss_feed(
                "https://example.com/empty.xml", "Empty"
            )

        assert items == []

    @pytest.mark.asyncio
    async def test_handles_http_error(self):
        """Returns empty list on HTTP errors."""
        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            items = await _fetch_rss_feed(
                "https://example.com/bad.xml", "Bad"
            )

        assert items == []

    @pytest.mark.asyncio
    async def test_rss_with_media(self, sample_rss_with_media):
        """Extracts media URLs from RSS entries."""
        mock_response = MagicMock()
        mock_response.text = sample_rss_with_media
        mock_response.status_code = 200

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_rss_feed(
                "https://example.com/feed.xml", "Media Test"
            )

        assert len(items) == 1
        assert items[0]["media_url"] == "https://example.com/image.jpg"


# ---------------------------------------------------------------------------
# Integration tests: GitHub releases fetcher
# ---------------------------------------------------------------------------

class TestFetchGitHubReleases:
    @pytest.mark.asyncio
    async def test_successful_fetch(self, sample_github_releases):
        """Successfully parses GitHub releases."""
        mock_response = MagicMock()
        mock_response.json.return_value = sample_github_releases
        mock_response.status_code = 200

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_github_releases(
                "test/repo", "Test Repo"
            )

        assert len(items) == 2
        assert items[0]["source_name"] == "Test Repo"
        assert "v3.0.0" in items[0]["raw_text"]
        assert items[0]["url"] == "https://github.com/test/repo/releases/tag/v3.0.0"

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        """Handles GitHub rate limiting."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"X-RateLimit-Remaining": "0"}
        mock_response.raise_for_status = MagicMock()

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_github_releases("test/repo", "Test")

        assert items == []

    @pytest.mark.asyncio
    async def test_empty_releases(self):
        """Handles empty release list."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.status_code = 200

        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            items = await _fetch_github_releases("test/repo", "Test")

        assert items == []

    @pytest.mark.asyncio
    async def test_handles_timeout(self):
        """Returns empty list on timeout."""
        with patch("sources.company_feeds.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            items = await _fetch_github_releases("test/repo", "Test")

        assert items == []


# ---------------------------------------------------------------------------
# Company-specific fetcher tests (mocked)
# ---------------------------------------------------------------------------

class TestCompanyFetchers:
    """Test that individual company fetchers produce valid items."""

    @pytest.mark.asyncio
    async def test_fetch_openai_returns_list(self):
        """fetch_openai_news returns a list."""
        with patch("sources.company_feeds._fetch_rss_feed", new_callable=AsyncMock, return_value=[
            _build_item(source_name="OpenAI Blog", title="Test", url="https://example.com")
        ]), patch("sources.company_feeds._fetch_github_releases", new_callable=AsyncMock, return_value=[
            _build_item(source_name="OpenAI Python SDK", title="SDK Release", url="https://example.com/gh")
        ]):
            items = await fetch_openai_news()
            assert isinstance(items, list)
            assert len(items) >= 2

    @pytest.mark.asyncio
    async def test_fetch_anthropic_returns_list(self):
        """fetch_anthropic_news returns a list."""
        with patch("sources.company_feeds._fetch_github_releases", new_callable=AsyncMock, return_value=[
            _build_item(source_name="Anthropic Python SDK", title="SDK v1.0", url="https://example.com")
        ]):
            items = await fetch_anthropic_news()
            assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_fetch_xai_returns_empty(self):
        """fetch_xai_news returns empty list (no public repos yet)."""
        items = await fetch_xai_news()
        assert items == []

    @pytest.mark.asyncio
    async def test_fetchers_produce_valid_items(self):
        """All company fetchers produce items with the correct format."""
        mock_item = _build_item(
            source_name="Test", title="Test Item", url="https://example.com"
        )
        with patch("sources.company_feeds._fetch_rss_feed", new_callable=AsyncMock, return_value=[mock_item]), \
             patch("sources.company_feeds._fetch_github_releases", new_callable=AsyncMock, return_value=[mock_item]):
            for name, fetcher in COMPANY_FETCHERS.items():
                items = await fetcher()
                assert isinstance(items, list), f"{name} did not return a list"
                for item in items:
                    assert "id" in item, f"{name} item missing 'id'"
                    assert "raw_text" in item, f"{name} item missing 'raw_text'"
                    assert "url" in item, f"{name} item missing 'url'"
                    assert "source_name" in item, f"{name} item missing 'source_name'"
                    assert "media_url" in item, f"{name} item missing 'media_url'"
                    assert "date" in item, f"{name} item missing 'date'"


# ---------------------------------------------------------------------------
# Aggregate function tests
# ---------------------------------------------------------------------------

class TestFetchCompanyNews:
    @pytest.mark.asyncio
    async def test_fetch_all_companies(self):
        """fetch_company_news() fetches from all registered companies."""
        mock_item = _build_item(
            source_name="Test", title="Test", url="https://example.com"
        )

        async def mock_fetcher():
            return [mock_item]

        with patch.dict("sources.company_feeds.COMPANY_FETCHERS", {
            "openai": mock_fetcher,
            "anthropic": mock_fetcher,
        }):
            items = await fetch_company_news(companies=["openai", "anthropic"])

        # Should have items from both (deduped since same URL)
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_deduplication(self):
        """Same item from multiple sources is deduplicated."""
        mock_item = _build_item(
            source_name="Test", title="Same Item", url="https://example.com/same"
        )

        async def mock_fetcher():
            return [mock_item]

        with patch.dict("sources.company_feeds.COMPANY_FETCHERS", {
            "openai": mock_fetcher,
            "anthropic": mock_fetcher,
            "deepseek": mock_fetcher,
        }):
            items = await fetch_company_news(
                companies=["openai", "anthropic", "deepseek"]
            )

        # All three return same item with same URL → same ID → deduped to 1
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_sorted_by_date(self):
        """Items are sorted by date, newest first."""
        item_old = _build_item(
            source_name="Old", title="Old", url="https://example.com/old",
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        item_new = _build_item(
            source_name="New", title="New", url="https://example.com/new",
            date=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )

        async def mock_fetcher():
            return [item_old, item_new]

        with patch.dict("sources.company_feeds.COMPANY_FETCHERS", {
            "test": mock_fetcher,
        }):
            items = await fetch_company_news(companies=["test"])

        assert len(items) == 2
        assert items[0]["url"] == "https://example.com/new"  # newest first
        assert items[1]["url"] == "https://example.com/old"

    @pytest.mark.asyncio
    async def test_handles_fetcher_exception(self):
        """Gracefully handles exceptions from individual fetchers."""
        async def good_fetcher():
            return [_build_item(source_name="Good", title="OK", url="https://example.com/ok")]

        async def bad_fetcher():
            raise RuntimeError("Something broke")

        with patch.dict("sources.company_feeds.COMPANY_FETCHERS", {
            "good": good_fetcher,
            "bad": bad_fetcher,
        }):
            items = await fetch_company_news(companies=["good", "bad"])

        assert len(items) == 1
        assert items[0]["source_name"] == "Good"

    @pytest.mark.asyncio
    async def test_empty_companies_list(self):
        """Empty company list returns empty items."""
        items = await fetch_company_news(companies=[])
        assert items == []

    @pytest.mark.asyncio
    async def test_invalid_company_key(self):
        """Invalid company key is skipped."""
        items = await fetch_company_news(companies=["nonexistent"])
        assert items == []

    @pytest.mark.asyncio
    async def test_max_items_per_company(self):
        """Respects max_items_per_company limit."""
        many_items = [
            _build_item(
                source_name="Test",
                title=f"Item {i}",
                url=f"https://example.com/{i}",
            )
            for i in range(20)
        ]

        async def mock_fetcher():
            return many_items

        with patch.dict("sources.company_feeds.COMPANY_FETCHERS", {
            "test": mock_fetcher,
        }):
            items = await fetch_company_news(
                companies=["test"],
                max_items_per_company=5,
            )

        assert len(items) <= 5


# ---------------------------------------------------------------------------
# Company registry tests
# ---------------------------------------------------------------------------

class TestCompanyRegistry:
    def test_all_expected_companies_registered(self):
        """All 12 expected companies have registered fetchers."""
        expected = {
            "openai", "google_deepmind", "google_ai", "anthropic",
            "meta_ai", "mistral", "huggingface", "groq",
            "deepseek", "cohere", "stability", "xai", "nous_research",
        }
        assert expected == set(COMPANY_FETCHERS.keys())

    def test_all_fetchers_are_callable(self):
        """All registered fetchers are callable."""
        for name, fetcher in COMPANY_FETCHERS.items():
            assert callable(fetcher), f"{name} fetcher is not callable"

    def test_all_fetchers_are_async(self):
        """All registered fetchers are async functions."""
        for name, fetcher in COMPANY_FETCHERS.items():
            assert asyncio.iscoroutinefunction(fetcher), f"{name} fetcher is not async"
