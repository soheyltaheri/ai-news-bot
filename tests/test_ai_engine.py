# tests/test_ai_engine.py
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from processor.ai_engine import process_news_with_ai
import processor.ai_engine
import config

@pytest.mark.asyncio
async def test_process_news_with_ai_no_key(monkeypatch):
    # Mock BOTH config and the imported ROUTER_API_KEY in ai_engine
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "")
    res = await process_news_with_ai("some text", "source")
    assert res is None

@pytest.mark.asyncio
async def test_process_news_with_ai_success(monkeypatch):
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "test-key")
    monkeypatch.setattr(processor.ai_engine, "ROUTER_BASE_URL", "https://api.test")
    monkeypatch.setattr(processor.ai_engine, "MODEL_NAME", "test-model")
    
    # We patch httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "title": "Test Title",
                            "category": "🤖 هوش مصنوعی",
                            "body": "Test body text with enough length.",
                            "rating": 5,
                            "is_relevant": True
                        })
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        result = await process_news_with_ai("raw news content", "source")
        assert result is not None
        assert result["title"] == "Test Title"
        assert result["category"] == "🤖 هوش مصنوعی"
        assert result["rating"] == 5
        assert result["is_relevant"] is True
        
        # Verify call arguments
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.test/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert kwargs["json"]["model"] == "test-model"

@pytest.mark.asyncio
async def test_process_news_with_ai_error(monkeypatch):
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "test-key")
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.RequestError("Network error")
        result = await process_news_with_ai("raw news content", "source")
        assert result is None

@pytest.mark.asyncio
async def test_process_news_with_ai_bad_json(monkeypatch):
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "test-key")
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Not a JSON object"
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        result = await process_news_with_ai("raw news content", "source")
        assert result is None
