# tests/test_extra_ai_engine.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from processor.ai_engine import process_news_with_ai, extract_and_parse_json
import processor.ai_engine

def test_extract_and_parse_json_varied():
    # Empty inputs
    assert extract_and_parse_json(None) is None
    assert extract_and_parse_json("") is None
    
    # Valid JSON
    assert extract_and_parse_json('{"key": "value"}') == {"key": "value"}
    
    # Markdown formatted JSON
    assert extract_and_parse_json('```json\n{"key": "value"}\n```') == {"key": "value"}
    assert extract_and_parse_json('```\n{"key": "value"}\n```') == {"key": "value"}
    
    # Surrounded by text
    assert extract_and_parse_json('some text {"key": "value"} other text') == {"key": "value"}
    
    # Broken JSON inside markdown or text
    assert extract_and_parse_json('```json\n{"key": "value"\n```') is None
    assert extract_and_parse_json('{"key": "value"') is None

@pytest.mark.asyncio
async def test_process_news_with_ai_missing_choices(monkeypatch):
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "test-key")
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"something": "else"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        result = await process_news_with_ai("raw news content", "source")
        assert result is None

@pytest.mark.asyncio
async def test_process_news_with_ai_rating_fallback(monkeypatch):
    monkeypatch.setattr(processor.ai_engine, "ROUTER_API_KEY", "test-key")
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"title": "Test", "body": "text", "rating": "invalid_rating", "is_relevant": true}'
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        result = await process_news_with_ai("raw news content", "source")
        assert result is not None
        assert result["rating"] == 3 # Default rating fallback
