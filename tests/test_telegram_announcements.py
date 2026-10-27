# tests/test_telegram_announcements.py
import pytest
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from telethon import TelegramClient
from sources.telegram_announcements import fetch_historical_announcements, start_announcements_monitoring

@pytest.mark.asyncio
async def test_fetch_historical_announcements_success():
    client_mock = MagicMock(spec=TelegramClient)
    
    # Mocking client.iter_messages async generator
    async def mock_iter_messages(*args, **kwargs):
        message = MagicMock()
        message.id = 111
        message.chat_id = -100999888
        message.date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        message.text = "This is an official AI company announcement that is long enough to satisfy our validation constraints."
        message.photo = "http://example.com/photo.jpg"
        message.chat = MagicMock()
        message.chat.username = "OpenAI_Updates"
        message.chat.title = "OpenAI Updates Channel"
        yield message

    client_mock.iter_messages = mock_iter_messages
    
    with patch("sources.telegram_announcements.is_processed", return_value=False), \
         patch("sources.telegram_announcements.ANNOUNCEMENT_CHANNELS", ["OpenAI_Updates"]):
         
         items = await fetch_historical_announcements(client_mock, days=5)
         assert len(items) == 1
         assert items[0]["id"] == "tg_ann_-100999888_111"
         assert items[0]["media_url"] == "https://t.me/OpenAI_Updates/111"
         assert items[0]["source_name"] == "OpenAI Updates Channel"
         assert items[0]["is_official_announcement"] is True

@pytest.mark.asyncio
async def test_start_announcements_monitoring_success():
    client_mock = MagicMock(spec=TelegramClient)
    
    # Mock for client.on
    def mock_on(event_handler):
        def decorator(func):
            client_mock.handler = func
            return func
        return decorator
    
    client_mock.on = mock_on
    
    with patch("sources.telegram_announcements.process_news_with_ai", new_callable=AsyncMock) as mock_process_ai, \
         patch("sources.telegram_announcements.is_processed", return_value=False), \
         patch("sources.telegram_announcements.add_to_queue") as mock_add, \
         patch("sources.telegram_announcements.mark_as_processed") as mock_mark:
         
         mock_process_ai.return_value = {
             'is_relevant': True,
             'title': 'AI Announcement Title',
             'body': 'This is the body of the announcement.',
             'rating': 3,
             'category': '🤖 هوش مصنوعی'
         }
         
         await start_announcements_monitoring(client_mock)
         
         # Trigger the handler manually with a mock event
         event = MagicMock()
         event.message = MagicMock()
         event.message.text = "This is a very long live message from OpenAI official updates that is over 50 characters."
         event.message.id = 222
         event.message.chat_id = -100999888
         event.message.photo = None
         event.chat = MagicMock()
         event.chat.username = "OpenAI_Updates"
         event.chat.title = "OpenAI Updates"
         
         await client_mock.handler(event)
         
         mock_process_ai.assert_called_once_with(event.message.text, "OpenAI Updates")
         mock_add.assert_called_once_with(
             title='AI Announcement Title',
             body='[CATEGORY_MARKER: 🤖 هوش مصنوعی] This is the body of the announcement.',
             media_url=None,
             source_url='https://t.me/OpenAI_Updates/222',
             source_name='OpenAI Updates',
             rating=4  # Rating should be boosted from 3 to 4 because it's an official announcement channel
         )
         mock_mark.assert_called_once()
