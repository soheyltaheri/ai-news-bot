# tests/test_enhanced_sources.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from sources.enhanced_sources import (
    fetch_from_hacker_news_api,
    fetch_from_reddit_ml,
    fetch_from_reddit_ai,
    fetch_rss_multiple_feeds,
    verify_telegram_channel_preview
)

@pytest.mark.asyncio
async def test_fetch_from_hacker_news_api_success():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("sources.enhanced_sources.is_processed", return_value=False):
         
         # Mocking top stories JSON return
         mock_response_ids = MagicMock(spec=httpx.Response)
         mock_response_ids.status_code = 200
         mock_response_ids.json.return_value = [1001, 1002]
         
         # Mocking item JSON return
         mock_response_item1 = MagicMock(spec=httpx.Response)
         mock_response_item1.status_code = 200
         mock_response_item1.json.return_value = {
             "id": 1001,
             "title": "New AI models released by startup",
             "url": "http://example.com/ai1"
         }
         mock_response_item2 = MagicMock(spec=httpx.Response)
         mock_response_item2.status_code = 200
         mock_response_item2.json.return_value = {
             "id": 1002,
             "title": "General tech story",
             "url": "http://example.com/non-tech"
         }
         
         mock_get.side_effect = [mock_response_ids, mock_response_item1, mock_response_item2]
         
         # Should only fetch item1 because filter_ai is True by default
         items = await fetch_from_hacker_news_api(limit=2, filter_ai=True)
         assert len(items) == 1
         assert items[0]["id"] == "hn_1001"
         assert items[0]["url"] == "http://example.com/ai1"
         assert "New AI models" in items[0]["raw_text"]

@pytest.mark.asyncio
async def test_fetch_from_reddit_success():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("sources.enhanced_sources.is_processed", return_value=False):
         
         mock_response = MagicMock(spec=httpx.Response)
         mock_response.status_code = 200
         mock_response.json.return_value = {
             "kind": "Listing",
             "data": {
                 "children": [
                     {
                         "kind": "t3",
                         "data": {
                             "id": "abc123",
                             "title": "New LLM breakthrough paper",
                             "selftext": "This is details about LLM",
                             "url": "http://example.com/paper",
                             "permalink": "/r/MachineLearning/comments/abc123/paper/",
                             "stickied": False
                         }
                     }
                 ]
             }
         }
         mock_get.return_value = mock_response
         
         items = await fetch_from_reddit_ml(limit=1)
         assert len(items) == 1
         assert items[0]["id"] == "reddit_MachineLearning_abc123"
         assert items[0]["url"] == "https://www.reddit.com/r/MachineLearning/comments/abc123/paper/"
         assert "New LLM breakthrough" in items[0]["raw_text"]

@pytest.mark.asyncio
async def test_fetch_rss_multiple_feeds_success():
    mock_feed = MagicMock()
    mock_feed.feed.title = "AI News Feed"
    mock_entry = MagicMock()
    mock_entry.link = "http://example.com/feed-item"
    mock_entry.title = "Feed Item 1"
    mock_entry.summary = "<p>Summary containing HTML</p>"
    mock_feed.entries = [mock_entry]
    
    with patch("feedparser.parse", return_value=mock_feed), \
         patch("sources.enhanced_sources.is_processed", return_value=False), \
         patch("sources.enhanced_sources.extract_media_url_from_entry", return_value="http://example.com/img.jpg"):
         
         items = await fetch_rss_multiple_feeds(["http://example.com/rss"])
         assert len(items) == 1
         assert items[0]["id"] == "http://example.com/feed-item"
         assert items[0]["source_name"] == "AI News Feed"
         assert "Summary containing HTML" in items[0]["raw_text"]
         assert items[0]["media_url"] == "http://example.com/img.jpg"

@pytest.mark.asyncio
async def test_verify_telegram_channel_preview_active():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = '<html><div class="tgme_widget_message_text">Hello AI world</div></html>'
        mock_get.return_value = mock_response
        
        is_active = await verify_telegram_channel_preview("DeepLearning_ai")
        assert is_active is True

@pytest.mark.asyncio
async def test_verify_telegram_channel_preview_inactive():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = '<html><div class="tgme_channel_info">No messages here</div></html>'
        mock_get.return_value = mock_response
        
        is_active = await verify_telegram_channel_preview("EmptyChannel")
        assert is_active is False

@pytest.mark.asyncio
async def test_fetch_github_trending_ai_success():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_response_python = MagicMock(spec=httpx.Response)
        mock_response_python.status_code = 200
        mock_response_python.text = """
        <html>
            <article class="Box-row">
                <h2 class="h3">
                    <a href="/HKUDS/Vibe-Trading">HKUDS / Vibe-Trading</a>
                </h2>
                <p class="col-9">Vibe-Trading: Your Personal Trading Agent</p>
                <div class="f6">
                    <a href="/HKUDS/Vibe-Trading/stargazers">21,492</a>
                </div>
            </article>
            <article class="Box-row">
                <h2 class="h3">
                    <a href="/General/Tech-Repo">General / Tech-Repo</a>
                </h2>
                <p class="col-9">Just a standard repository with some code</p>
                <div class="f6">
                    <a href="/General/Tech-Repo/stargazers">1,234</a>
                </div>
            </article>
        </html>
        """
        
        mock_response_generic = MagicMock(spec=httpx.Response)
        mock_response_generic.status_code = 200
        mock_response_generic.text = """
        <html>
            <article class="Box-row">
                <h2 class="h3">
                    <a href="/FoundationAgents/OpenManus">FoundationAgents / OpenManus</a>
                </h2>
                <p class="col-9">An AI-powered autonomous agent.</p>
                <div class="f6">
                    <a href="/FoundationAgents/OpenManus/stargazers">57,354</a>
                </div>
            </article>
        </html>
        """
        
        mock_get.side_effect = [mock_response_python, mock_response_generic]
        
        from sources.enhanced_sources import fetch_github_trending_ai
        items = await fetch_github_trending_ai(limit=5)
        
        assert len(items) == 2
        assert items[0]['id'] == 'gh_trend_HKUDS_Vibe-Trading'
        assert items[0]['url'] == 'https://github.com/HKUDS/Vibe-Trading'
        assert 'Vibe-Trading' in items[0]['raw_text']
        assert items[1]['id'] == 'gh_trend_FoundationAgents_OpenManus'
        assert items[1]['url'] == 'https://github.com/FoundationAgents/OpenManus'
