# tests/test_main.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import main
import config
from database.dedup import init_db, add_to_queue, get_next_pending_post

@pytest.mark.asyncio
async def test_process_raw_item_shutdown():
    main.shutdown_event.set()
    res = await main.process_raw_item({"id": "1", "raw_text": "text", "source_name": "src"})
    assert res is None
    main.shutdown_event.clear()

@pytest.mark.asyncio
async def test_process_raw_item_low_rating(tmp_path):
    test_db = tmp_path / "posts_main.db"
    with patch("config.DB_PATH", str(test_db)):
        init_db()
        item = {
            'id': 'test_low_rating',
            'raw_text': 'This is raw text of a news.',
            'source_name': 'TechNews',
            'media_url': None,
            'url': 'http://tech.news/1',
            'is_official_announcement': False
        }
        
        ai_response = {
            'title': 'Low Rating Title',
            'body': 'Low rating body',
            'rating': 2,
            'is_relevant': True
        }
        
        with patch('main.process_news_with_ai', AsyncMock(return_value=ai_response)):
            await main.process_raw_item(item)
            assert get_next_pending_post() is None

@pytest.mark.asyncio
async def test_process_raw_item_official_low_rating_boosted(tmp_path):
    test_db = tmp_path / "posts_main_boosted.db"
    with patch("config.DB_PATH", str(test_db)):
        init_db()
        item = {
            'id': 'test_official_low_rating',
            'raw_text': 'This is raw text of an official news.',
            'source_name': 'OfficialNews',
            'media_url': None,
            'url': 'http://official.news/1',
            'is_official_announcement': True
        }
        
        ai_response = {
            'title': 'Official Title',
            'body': 'Official body',
            'rating': 2,
            'is_relevant': True
        }
        
        with patch('main.process_news_with_ai', AsyncMock(return_value=ai_response)):
            await main.process_raw_item(item)
            post = get_next_pending_post()
            assert post is not None
            assert post[6] == 4  # Rating should be boosted to 4

@pytest.mark.asyncio
async def test_process_raw_item_error(tmp_path):
    test_db = tmp_path / "posts_main_err.db"
    with patch("config.DB_PATH", str(test_db)):
        init_db()
        item = {
            'id': 'test_err',
            'raw_text': 'Raw text.',
            'source_name': 'TechNews',
            'media_url': None,
            'url': 'http://tech.news/1'
        }
        
        with patch('main.process_news_with_ai', AsyncMock(side_effect=ValueError("Process error"))):
            await main.process_raw_item(item)
            assert get_next_pending_post() is None

@pytest.mark.asyncio
async def test_queue_sender_worker_send_fails(tmp_path):
    test_db = tmp_path / "posts_worker_fail.db"
    with patch("config.DB_PATH", str(test_db)):
        init_db()
        add_to_queue("Title W", "Body W", None, "http://w", "Src W", 4)
        
        client_mock = MagicMock()
        client_mock.is_connected.return_value = True
        
        # We mock post_to_channel to return False.
        # To avoid the 30 seconds wait, we mock asyncio.wait_for to raise immediately
        # or we mock shutdown_event to be set.
        # But wait! queue_sender_worker checks `not shutdown_event.is_set()` in the loop header.
        # If we set shutdown_event inside the mock of post_to_channel, the loop will terminate immediately after that turn.
        
        main.shutdown_event.clear()
        
        async def mock_post(*args, **kwargs):
            main.shutdown_event.set()
            return False

        with patch("main.post_to_channel", AsyncMock(side_effect=mock_post)):
            await main.queue_sender_worker(client_mock)
            
            # Since it failed, post should still be pending
            assert get_next_pending_post() is not None

@pytest.mark.asyncio
async def test_queue_sender_worker_exception(tmp_path):
    test_db = tmp_path / "posts_worker_exc.db"
    with patch("config.DB_PATH", str(test_db)):
        init_db()
        add_to_queue("Title W", "Body W", None, "http://w", "Src W", 4)
        
        client_mock = MagicMock()
        client_mock.is_connected.return_value = True
        
        main.shutdown_event.clear()
        
        async def mock_post_exc(*args, **kwargs):
            main.shutdown_event.set()
            raise RuntimeError("Worker error")

        with patch("main.post_to_channel", AsyncMock(side_effect=mock_post_exc)):
            await main.queue_sender_worker(client_mock)
            
            # Since it raised exception, post should still be pending
            assert get_next_pending_post() is not None

@pytest.mark.asyncio
async def test_web_fetching_loop_flow():
    hn_mock_item = {
        'id': 'hn_test_123',
        'raw_text': 'Hacker news raw text',
        'url': 'http://hn.com',
        'source_name': 'Hacker News',
        'media_url': None
    }
    
    main.shutdown_event.clear()
    
    async def mock_process_item(item):
        main.shutdown_event.set()
        
    with patch("main.fetch_hacker_news_top", AsyncMock(return_value=[hn_mock_item])) as mock_hn, \
         patch("main.fetch_rss_feeds", AsyncMock(return_value=[])) as mock_rss, \
         patch("main.process_raw_item", AsyncMock(side_effect=mock_process_item)) as mock_process:
         
         await main.web_fetching_loop()
         
         mock_hn.assert_called_once()
         mock_process.assert_called_once_with(hn_mock_item)
