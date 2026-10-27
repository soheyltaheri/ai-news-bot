"""
company_feeds.py — Direct source extraction from AI company official channels.

These companies publish news/updates on their own platforms BEFORE they reach
Telegram/news aggregators. This module scrapes them directly for breaking news.

Supported companies and their sources (verified July 2026):
  1. OpenAI        — RSS: openai.com/news/rss + GitHub: openai/openai-python
  2. Google DeepMind — RSS: deepmind.google/blog/rss.xml + Google AI Blog RSS
  3. Anthropic      — GitHub: anthropics/anthropic-sdk-python + anthropics/claude-cookbooks
  4. Meta AI        — GitHub: meta-llama/llama + meta-llama/llama-models
  5. Mistral AI     — GitHub: mistralai/mistral-inference
  6. Hugging Face   — RSS: huggingface.co/blog/feed.xml + GitHub: huggingface/transformers
  7. Groq           — GitHub: groq/groq-python
  8. DeepSeek       — GitHub: deepseek-ai/DeepSeek-V3
  9. Cohere         — GitHub: cohere-ai/cohere-python
  10. Stability AI  — GitHub: Stability-AI/stability-sdk
  11. xAI (Grok)    — GitHub: xAI (no public releases yet, placeholder)
  12. Nous Research — GitHub: NousResearch repos + HuggingFace blog

Usage:
    from sources.company_feeds import fetch_company_news
    items = await fetch_company_news()

    # Or fetch specific companies:
    from sources.company_feeds import fetch_openai_news, fetch_github_releases
    openai_items = await fetch_openai_news()
"""

import asyncio
import hashlib
import re
import logging
from datetime import datetime, timezone
from typing import Optional
from email.utils import parsedate_to_datetime

import httpx
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiting / retry helpers
# ---------------------------------------------------------------------------

# Global semaphore to cap concurrent outbound requests
_OUTBOUND_SEMAPHORE = asyncio.Semaphore(8)

# Per-domain rate limiter: last-request timestamp dict
_last_request: dict[str, float] = {}
_MIN_DOMAIN_DELAY = 1.0  # seconds between requests to the same domain

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _dedup_id(raw: str) -> str:
    """Generate a deterministic dedup ID from the URL or title."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _rate_limited_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 2,
    backoff: float = 2.0,
) -> httpx.Response:
    """GET with per-domain rate limiting and automatic retry on transient errors."""
    import time

    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""

    for attempt in range(retries + 1):
        # Per-domain delay
        now = time.monotonic()
        last = _last_request.get(domain, 0)
        wait = _MIN_DOMAIN_DELAY - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)

        async with _OUTBOUND_SEMAPHORE:
            try:
                _last_request[domain] = time.monotonic()
                resp = await client.get(
                    url,
                    headers=_HEADERS,
                    follow_redirects=True,
                    timeout=timeout,
                )
                if resp.status_code == 429:
                    # Rate-limited — back off
                    retry_after = float(resp.headers.get("Retry-After", backoff * (attempt + 1)))
                    logger.warning("Rate limited on %s, sleeping %.1fs", domain, retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if resp.status_code >= 500 and attempt < retries:
                    logger.warning("Server error %d on %s, retrying...", resp.status_code, domain)
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.TimeoutException:
                if attempt < retries:
                    logger.warning("Timeout on %s (attempt %d), retrying...", url, attempt + 1)
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                raise
            except httpx.HTTPStatusError:
                raise

    # Should not reach here, but just in case
    raise httpx.HTTPError(f"All {retries + 1} attempts failed for {url}")


# ---------------------------------------------------------------------------
# Item builder — ensures consistent format matching main.py expectations
# ---------------------------------------------------------------------------

def _build_item(
    *,
    source_name: str,
    title: str,
    url: str,
    summary: str = "",
    date: Optional[datetime] = None,
    media_url: Optional[str] = None,
) -> dict:
    """Build a standardized news item matching the project's item format."""
    item_id = _dedup_id(url or title)
    raw_text = f"Title: {title}"
    if summary:
        raw_text += f"\nSummary: {summary}"

    return {
        "id": item_id,
        "raw_text": raw_text,
        "url": url,
        "source_name": source_name,
        "media_url": media_url,
        "date": date or datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# RSS feed fetcher (generic)
# ---------------------------------------------------------------------------

async def _fetch_rss_feed(
    url: str,
    source_name: str,
    max_entries: int = 10,
) -> list[dict]:
    """Fetch and parse an RSS/Atom feed, returning items in standard format."""
    items = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await _rate_limited_get(client, url)
            feed = feedparser.parse(resp.text)

            if feed.bozo and not feed.entries:
                logger.warning("RSS parse error for %s: %s", url, feed.bozo_exception)
                return []

            for entry in feed.entries[:max_entries]:
                link = entry.get("link", "")
                title = entry.get("title", "").strip()
                if not title or not link:
                    continue

                # Parse date
                date = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                if not date and hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    try:
                        date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass

                # Summary
                summary = entry.get("summary", "") or entry.get("description", "")
                if summary:
                    summary = BeautifulSoup(summary, "html.parser").get_text(strip=True)[:500]

                # Media URL
                media_url = _extract_media_from_rss_entry(entry)

                items.append(_build_item(
                    source_name=source_name,
                    title=title,
                    url=link,
                    summary=summary,
                    date=date,
                    media_url=media_url,
                ))

    except Exception as e:
        logger.error("Error fetching RSS %s: %s", url, e)

    return items


def _extract_media_from_rss_entry(entry) -> Optional[str]:
    """Extract image/media URL from an RSS entry."""
    # 1. media_content / media_thumbnail
    for attr in ("media_content", "media_thumbnail"):
        media_list = getattr(entry, attr, None)
        if media_list:
            for m in media_list:
                if isinstance(m, dict) and m.get("url"):
                    return m["url"]

    # 2. enclosures
    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if isinstance(enc, dict) and "image" in enc.get("type", ""):
                return enc.get("href") or enc.get("url")

    # 3. og:image from summary HTML
    summary = entry.get("summary", "")
    if summary:
        soup = BeautifulSoup(summary, "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            return img["src"]

    return None


# ---------------------------------------------------------------------------
# GitHub releases fetcher (generic)
# ---------------------------------------------------------------------------

async def _fetch_github_releases(
    repo: str,
    source_name: str,
    max_releases: int = 3,
) -> list[dict]:
    """
    Fetch latest releases from a GitHub repo via the public API.

    Args:
        repo: "owner/repo" format, e.g. "openai/openai-python"
        source_name: Human-readable name for the item
        max_releases: Max releases to return
    """
    url = f"https://api.github.com/repos/{repo}/releases?per_page={max_releases}"
    items = []

    try:
        async with httpx.AsyncClient() as client:
            resp = await _rate_limited_get(client, url, timeout=20.0)

            # Handle GitHub rate limiting gracefully
            if resp.status_code == 403:
                remaining = resp.headers.get("X-RateLimit-Remaining", "?")
                logger.warning(
                    "GitHub rate limit hit for %s (remaining: %s). "
                    "Consider setting GITHUB_TOKEN env var.",
                    repo, remaining,
                )
                return []

            releases = resp.json()
            if not isinstance(releases, list):
                return []

            for rel in releases[:max_releases]:
                tag = rel.get("tag_name", "")
                name = rel.get("name", tag)
                html_url = rel.get("html_url", "")
                body = rel.get("body", "") or ""
                published = rel.get("published_at", "")

                if not tag or not html_url:
                    continue

                date = None
                if published:
                    try:
                        date = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        pass

                # Truncate body for raw_text
                body_clean = BeautifulSoup(body, "html.parser").get_text(strip=True)[:500]

                items.append(_build_item(
                    source_name=source_name,
                    title=f"{repo} {name or tag}",
                    url=html_url,
                    summary=body_clean,
                    date=date,
                ))

    except Exception as e:
        logger.error("Error fetching GitHub releases for %s: %s", repo, e)

    return items


# ---------------------------------------------------------------------------
# Individual company fetchers
# ---------------------------------------------------------------------------

async def fetch_openai_news() -> list[dict]:
    """
    Fetch OpenAI news from their RSS feed and Python SDK releases.

    Sources:
      - RSS: https://openai.com/news/rss (redirects from /blog/rss.xml)
      - GitHub: openai/openai-python releases
    """
    rss_url = "https://openai.com/news/rss"
    github_repo = "openai/openai-python"

    rss_items, gh_items = await asyncio.gather(
        _fetch_rss_feed(rss_url, "OpenAI Blog", max_entries=8),
        _fetch_github_releases(github_repo, "OpenAI Python SDK", max_releases=2),
    )
    return rss_items + gh_items


async def fetch_google_deepmind_news() -> list[dict]:
    """
    Fetch Google DeepMind news from their RSS feed and Gemma/Gemma repo.

    Sources:
      - RSS: https://deepmind.google/blog/rss.xml
      - GitHub: google-deepmind/gemma releases (for open model updates)
    """
    rss_url = "https://deepmind.google/blog/rss.xml"
    github_repo = "google-deepmind/gemma"

    rss_items, gh_items = await asyncio.gather(
        _fetch_rss_feed(rss_url, "Google DeepMind Blog", max_entries=8),
        _fetch_github_releases(github_repo, "Google Gemma", max_releases=2),
    )
    return rss_items + gh_items


async def fetch_anthropic_news() -> list[dict]:
    """
    Fetch Anthropic news from GitHub releases.

    Anthropic does NOT have a public RSS feed (verified 404 on /rss.xml).
    They publish via their website (anthropic.com/news) and GitHub.

    Sources:
      - GitHub: anthropics/anthropic-sdk-python releases
      - GitHub: anthropics/claude-code (Claude Code SDK)
    """
    repos = [
        ("anthropics/anthropic-sdk-python", "Anthropic Python SDK"),
        ("anthropics/claude-code", "Claude Code"),
    ]

    tasks = [
        _fetch_github_releases(repo, name, max_releases=2)
        for repo, name in repos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_meta_ai_news() -> list[dict]:
    """
    Fetch Meta AI / LLaMA news from GitHub releases.

    Meta AI does NOT have a public RSS feed (ai.meta.com/blog is JS-rendered).
    Primary source: GitHub releases from Meta LLaMA repositories.

    Sources:
      - GitHub: meta-llama/llama releases
      - GitHub: meta-llama/llama-models releases
    """
    repos = [
        ("meta-llama/llama", "Meta LLaMA"),
        ("meta-llama/llama-models", "Meta LLaMA Models"),
        ("meta-llama/llama-stack", "Meta LLaMA Stack"),
    ]

    tasks = [
        _fetch_github_releases(repo, name, max_releases=2)
        for repo, name in repos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_mistral_news() -> list[dict]:
    """
    Fetch Mistral AI news from GitHub releases.

    Mistral AI does NOT have a public RSS feed (404 on mistral.ai/news/rss.xml).

    Sources:
      - GitHub: mistralai/mistral-inference releases
    """
    return await _fetch_github_releases(
        "mistralai/mistral-inference",
        "Mistral AI",
        max_releases=3,
    )


async def fetch_huggingface_news() -> list[dict]:
    """
    Fetch Hugging Face news from their blog RSS and transformers releases.

    Sources:
      - RSS: https://huggingface.co/blog/feed.xml
      - GitHub: huggingface/transformers releases
    """
    rss_url = "https://huggingface.co/blog/feed.xml"
    github_repo = "huggingface/transformers"

    rss_items, gh_items = await asyncio.gather(
        _fetch_rss_feed(rss_url, "Hugging Face Blog", max_entries=8),
        _fetch_github_releases(github_repo, "HF Transformers", max_releases=2),
    )
    return rss_items + gh_items


async def fetch_groq_news() -> list[dict]:
    """
    Fetch Groq news from GitHub releases.

    Sources:
      - GitHub: groq/groq-python releases
    """
    return await _fetch_github_releases(
        "groq/groq-python",
        "Groq",
        max_releases=2,
    )


async def fetch_deepseek_news() -> list[dict]:
    """
    Fetch DeepSeek news from GitHub releases.

    Sources:
      - GitHub: deepseek-ai/DeepSeek-V3 releases
      - GitHub: deepseek-ai/DeepSeek-R1 releases
    """
    repos = [
        ("deepseek-ai/DeepSeek-V3", "DeepSeek V3"),
        ("deepseek-ai/DeepSeek-R1", "DeepSeek R1"),
    ]

    tasks = [
        _fetch_github_releases(repo, name, max_releases=2)
        for repo, name in repos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_cohere_news() -> list[dict]:
    """
    Fetch Cohere news from GitHub releases.

    Cohere's blog (docs.cohere.com/blog) does not offer an RSS feed.

    Sources:
      - GitHub: cohere-ai/cohere-python releases
    """
    return await _fetch_github_releases(
        "cohere-ai/cohere-python",
        "Cohere",
        max_releases=2,
    )


async def fetch_stability_news() -> list[dict]:
    """
    Fetch Stability AI news from GitHub releases.

    Sources:
      - GitHub: Stability-AI/stability-sdk releases
      - GitHub: Stability-AI/generative-models releases
    """
    repos = [
        ("Stability-AI/stability-sdk", "Stability AI SDK"),
        ("Stability-AI/generative-models", "Stability AI Models"),
    ]

    tasks = [
        _fetch_github_releases(repo, name, max_releases=2)
        for repo, name in repos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_xai_news() -> list[dict]:
    """
    Fetch xAI (Grok) news from GitHub releases.

    xAI's blog (x.ai/blog) blocks automated access (403).

    Sources:
      - GitHub: xAI releases (placeholder — no public repos yet)
      - TODO: Add when xAI publishes public repos
    """
    # xAI doesn't have public GitHub repos with releases yet as of July 2026.
    # This is a placeholder that can be activated when they do.
    # Uncomment below when xAI publishes GitHub repos:
    # return await _fetch_github_releases("xai-org/grok", "xAI Groq", max_releases=3)
    logger.debug("xAI: No public GitHub releases available yet")
    return []


async def fetch_nous_research_news() -> list[dict]:
    """
    Fetch Nous Research news from GitHub releases.

    Nous Research publishes models primarily on HuggingFace, not via GitHub
    releases. We monitor their GitHub repos for any release activity.

    Sources:
      - GitHub: NousResearch/hermes-agent releases
      - GitHub: NousResearch/Hermes-Function-Calling releases
    """
    repos = [
        ("NousResearch/hermes-agent", "Nous Hermes Agent"),
        ("NousResearch/Hermes-Function-Calling", "Nous Hermes FC"),
    ]

    tasks = [
        _fetch_github_releases(repo, name, max_releases=2)
        for repo, name in repos
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    items = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    return items


async def fetch_google_ai_blog_news() -> list[dict]:
    """
    Fetch Google AI Blog news from their RSS feed.

    Sources:
      - RSS: https://blog.google/technology/ai/rss/
    """
    return await _fetch_rss_feed(
        "https://blog.google/technology/ai/rss/",
        "Google AI Blog",
        max_entries=8,
    )


# ---------------------------------------------------------------------------
# Company registry — maps company names to their fetch functions
# ---------------------------------------------------------------------------

COMPANY_FETCHERS: dict[str, callable] = {
    "openai": fetch_openai_news,
    "google_deepmind": fetch_google_deepmind_news,
    "google_ai": fetch_google_ai_blog_news,
    "anthropic": fetch_anthropic_news,
    "meta_ai": fetch_meta_ai_news,
    "mistral": fetch_mistral_news,
    "huggingface": fetch_huggingface_news,
    "groq": fetch_groq_news,
    "deepseek": fetch_deepseek_news,
    "cohere": fetch_cohere_news,
    "stability": fetch_stability_news,
    "xai": fetch_xai_news,
    "nous_research": fetch_nous_research_news,
}


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

async def fetch_company_news(
    companies: Optional[list[str]] = None,
    max_items_per_company: int = 10,
) -> list[dict]:
    """
    Fetch news from ALL (or selected) AI company official channels.

    This is the primary entry point. It checks RSS feeds, GitHub releases,
    and blog APIs for the latest announcements from each company.

    Args:
        companies: Optional list of company keys to fetch.
                   If None, fetches from ALL companies.
                   Valid keys: "openai", "google_deepmind", "anthropic",
                   "meta_ai", "mistral", "huggingface", "groq", "deepseek",
                   "cohere", "stability", "xai", "nous_research", "google_ai"
        max_items_per_company: Maximum items to keep per company (post-filter).

    Returns:
        List of item dicts matching the project format:
        {id, raw_text, url, source_name, media_url, date}
    """
    targets = list(COMPANY_FETCHERS.keys()) if companies is None else companies
    fetchers = {k: v for k, v in COMPANY_FETCHERS.items() if k in targets}

    if not fetchers:
        logger.warning("No valid companies specified. Valid: %s", list(COMPANY_FETCHERS.keys()))
        return []

    logger.info("Fetching news from %d companies: %s", len(fetchers), list(fetchers.keys()))

    # Run all company fetchers concurrently
    tasks = {name: func() for name, func in fetchers.items()}
    results = await asyncio.gather(
        *tasks.values(),
        return_exceptions=True,
    )

    all_items: list[dict] = []
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            logger.error("Error fetching %s: %s", name, result)
            continue
        if isinstance(result, list):
            items = result[:max_items_per_company]
            all_items.extend(items)
            logger.info("  %s: %d items", name, len(items))

    # Deduplicate by ID across all companies
    seen_ids: set[str] = set()
    unique_items: list[dict] = []
    for item in all_items:
        item_id = item["id"]
        if item_id not in seen_ids:
            seen_ids.add(item_id)
            unique_items.append(item)

    # Sort by date (newest first)
    unique_items.sort(
        key=lambda x: x.get("date") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    logger.info(
        "Total company news: %d items (%d unique)",
        len(all_items), len(unique_items),
    )
    return unique_items


# ---------------------------------------------------------------------------
# Convenience: integration with the main bot's web_fetching_loop
# ---------------------------------------------------------------------------

async def fetch_company_news_loop(shutdown_event: asyncio.Event) -> list[dict]:
    """
    Fetch company news once, suitable for calling from the main bot loop.

    Returns items in the same format as fetch_rss_feeds() and
    fetch_hacker_news_top(), so they can be fed directly to process_raw_item().

    This function is designed to be called periodically (e.g., every 5-10 min).
    """
    if shutdown_event.is_set():
        return []

    try:
        items = await fetch_company_news()
        return items
    except Exception as e:
        logger.error("Error in company news fetch loop: %s", e)
        return []


# ---------------------------------------------------------------------------
# CLI entrypoint for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def _main():
        print("🔍 Fetching AI company news...")
        items = await fetch_company_news()
        print(f"\n📰 Found {len(items)} items:\n")
        for item in items[:20]:
            date_str = item["date"].strftime("%Y-%m-%d %H:%M") if item["date"] else "N/A"
            print(f"  [{item['source_name']}] {date_str}")
            print(f"    {item['raw_text'][:120]}")
            print(f"    {item['url']}")
            print()

    asyncio.run(_main())
