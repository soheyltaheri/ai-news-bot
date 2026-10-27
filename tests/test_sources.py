# tests/test_sources.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import datetime
from sources.web_sources import fetch_hacker_news_top, fetch_rss_feeds
from sources.telegram import (
    fetch_historical_telegram_posts, 
    start_telegram_monitoring,
    check_channel_join_or_preview,
    get_channel_and_post_url
)
from telethon import TelegramClient

@pytest.mark.asyncio
async def test_fetch_hacker_news_top_success():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("sources.web_sources.is_processed", return_value=False):
         
         # Mocking top stories JSON return
         mock_response_ids = MagicMock(spec=httpx.Response)
         mock_response_ids.json.return_value = [12345, 67890]
         
         # Mocking item JSON return
         mock_response_item1 = MagicMock(spec=httpx.Response)
         mock_response_item1.json.return_value = {
             "id": 12345,
             "title": "HN AI breakthrough",
             "url": "http://example.com/hn1"
         }
         mock_response_item2 = MagicMock(spec=httpx.Response)
         mock_response_item2.json.return_value = {
             "id": 67890,
             "title": "Another tech news",
             "url": "http://example.com/hn2"
         }
         
         mock_get.side_effect = [mock_response_ids, mock_response_item1, mock_response_item2]
         
         items = await fetch_hacker_news_top()
         assert len(items) == 2
         assert items[0]["id"] == "hn_12345"
         assert items[0]["url"] == "http://example.com/hn1"
         assert "HN AI breakthrough" in items[0]["raw_text"]

@pytest.mark.asyncio
async def test_fetch_hacker_news_top_error():
    with patch("httpx.AsyncClient.get", side_effect=Exception("API error")):
        items = await fetch_hacker_news_top()
        assert items == []

@pytest.mark.asyncio
async def test_fetch_rss_feeds_success():
    # We mock feedparser.parse
    mock_feed = MagicMock()
    mock_feed.feed.title = "Test Feed Title"
    mock_entry1 = MagicMock()
    mock_entry1.link = "http://example.com/rss1"
    mock_entry1.title = "RSS Item 1"
    mock_entry1.summary = "RSS Summary 1"
    
    mock_feed.entries = [mock_entry1]
    
    with patch("feedparser.parse", return_value=mock_feed), \
         patch("sources.web_sources.is_processed", return_value=False):
         
         items = await fetch_rss_feeds(["http://myfeed.com"])
         assert len(items) == 1
         assert items[0]["id"] == "http://example.com/rss1"
         assert items[0]["source_name"] == "Test Feed Title"
         assert "RSS Item 1" in items[0]["raw_text"]

@pytest.mark.asyncio
async def test_fetch_rss_feeds_error():
    with patch("feedparser.parse", side_effect=Exception("Parse error")):
        items = await fetch_rss_feeds(["http://myfeed.com"])
        assert items == []

@pytest.mark.asyncio
async def test_fetch_historical_telegram_posts_success():
    client_mock = MagicMock(spec=TelegramClient)
    
    # Mocking client.iter_messages async generator
    async def mock_iter_messages(*args, **kwargs):
        # Yield one message
        message = MagicMock()
        message.id = 999
        message.chat_id = -100123456
        message.grouped_id = None
        message.date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        message.text = "This is a very long telegram post text that is longer than fifty characters to pass validation checks."
        message.photo = "http://example.com/photo.jpg"
        message.chat = MagicMock()
        message.chat.username = "source_chan"
        message.chat.title = "Source Channel Title"
        yield message

    client_mock.iter_messages = mock_iter_messages
    client_mock.get_entity = AsyncMock(return_value=MagicMock())
    
    with patch("sources.telegram.is_processed", return_value=False), \
         patch("sources.telegram.SOURCE_CHANNELS", ["source_chan"]):
         
         items = await fetch_historical_telegram_posts(client_mock, days=5)
         assert len(items) == 1
         assert items[0]["id"] == "tg_-100123456_999"
         assert items[0]["media_url"] == "https://t.me/source_chan/999"  # We now store telegram post link format
         assert items[0]["source_name"] == "Source Channel Title"

@pytest.mark.asyncio
async def test_fetch_historical_telegram_posts_error():
    client_mock = MagicMock(spec=TelegramClient)
    client_mock.get_entity = AsyncMock(side_effect=Exception("Entity not found"))
    with patch("sources.telegram.SOURCE_CHANNELS", ["source_chan"]), \
         patch("sources.enhanced_sources.verify_telegram_channel_preview", return_value=False):
        items = await fetch_historical_telegram_posts(client_mock, days=5)
        assert items == []

@pytest.mark.asyncio
async def test_check_channel_join_or_preview():
    client_mock = MagicMock(spec=TelegramClient)
    client_mock.get_entity = AsyncMock(return_value=MagicMock())
    res = await check_channel_join_or_preview(client_mock, "testchan")
    assert res is True

    client_mock.get_entity = AsyncMock(side_effect=Exception("Private channel"))
    with patch("sources.enhanced_sources.verify_telegram_channel_preview", return_value=True):
        res = await check_channel_join_or_preview(client_mock, "testchan")
        assert res is True

    with patch("sources.enhanced_sources.verify_telegram_channel_preview", return_value=False):
        res = await check_channel_join_or_preview(client_mock, "testchan")
        assert res is False

def test_get_channel_and_post_url():
    # 1. Message with single photo
    msg = MagicMock()
    msg.id = 123
    msg.chat_id = -100111
    msg.grouped_id = None
    msg.photo = MagicMock()
    msg.document = None
    msg.chat = MagicMock()
    msg.chat.username = "testchan"
    
    source_url, media_url = get_channel_and_post_url("testchan", msg)
    assert source_url == "https://t.me/testchan/123"
    assert media_url == "https://t.me/testchan/123"

    # 2. Message with no photo/doc/group
    msg2 = MagicMock()
    msg2.id = 124
    msg2.chat_id = -100111
    msg2.grouped_id = None
    msg2.photo = None
    msg2.document = None
    msg2.chat = MagicMock()
    msg2.chat.username = "testchan"
    
    source_url2, media_url2 = get_channel_and_post_url("testchan", msg2)
    assert source_url2 == "https://t.me/testchan/124"
    assert media_url2 is None

    # 3. Message with image document
    msg3 = MagicMock()
    msg3.id = 125
    msg3.chat_id = -100111
    msg3.grouped_id = None
    msg3.photo = None
    msg3.document = MagicMock()
    msg3.document.mime_type = "image/png"
    msg3.chat = MagicMock()
    msg3.chat.username = "testchan"
    
    source_url3, media_url3 = get_channel_and_post_url("testchan", msg3)
    assert source_url3 == "https://t.me/testchan/125"
    assert media_url3 == "https://t.me/testchan/125"

    # 4. Message in a media group
    msg4 = MagicMock()
    msg4.id = 126
    msg4.chat_id = -100111
    msg4.grouped_id = 987654
    msg4.photo = None
    msg4.document = None
    msg4.chat = MagicMock()
    msg4.chat.username = "testchan"
    
    source_url4, media_url4 = get_channel_and_post_url("testchan", msg4)
    assert source_url4 == "https://t.me/testchan/126"
    assert media_url4 == "https://t.me/testchan/126"
