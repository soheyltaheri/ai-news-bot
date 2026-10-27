# tests/test_channel.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import asyncio
from telethon.errors import (
    FloodWaitError, 
    RPCError, 
    ServerError, 
    ChannelInvalidError,
    ChatAdminRequiredError
)
from poster.channel import format_post, post_to_channel
import config

def test_format_post_non_int_rating():
    ai_data = {
        'title': 'Test Title',
        'body': 'Test Body',
        'rating': 'invalid_rating'
    }
    formatted = format_post(ai_data, "https://example.com", "Src")
    assert "⭐⭐⭐" in formatted # Default is 3

@pytest.mark.asyncio
async def test_post_to_channel_none_client():
    res = await post_to_channel(None, "Text")
    assert res is False

@pytest.mark.asyncio
async def test_post_to_channel_connect_fail():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = False
    client_mock.connect = AsyncMock(side_effect=Exception("Connection failed"))
    res = await post_to_channel(client_mock, "Text")
    assert res is False

@pytest.mark.asyncio
async def test_post_to_channel_flood_wait_short():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    
    err = FloodWaitError(request=None)
    err.seconds = 1
    
    client_mock.send_message = AsyncMock(side_effect=[err, MagicMock()])
    
    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await post_to_channel(client_mock, "Text")
        assert res is True
        mock_sleep.assert_called_once_with(1)

@pytest.mark.asyncio
async def test_post_to_channel_flood_wait_long():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    
    err = FloodWaitError(request=None)
    err.seconds = 301
    
    client_mock.send_message = AsyncMock(side_effect=err)
    
    res = await post_to_channel(client_mock, "Text")
    assert res is False

@pytest.mark.asyncio
async def test_post_to_channel_rpc_error_retry_success():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    client_mock.send_message = AsyncMock(side_effect=[RPCError(request=None, message="RPC Error"), MagicMock()])
    
    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await post_to_channel(client_mock, "Text")
        assert res is True
        mock_sleep.assert_called_once_with(5)

@pytest.mark.asyncio
async def test_post_to_channel_rpc_error_retry_exhausted():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    client_mock.send_message = AsyncMock(side_effect=RPCError(request=None, message="RPC Error"))
    
    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await post_to_channel(client_mock, "Text", max_retries=2)
        assert res is False
        assert mock_sleep.call_count == 1 # 1st retry delay is 5s, 2nd attempt fails and returns False

@pytest.mark.asyncio
async def test_post_to_channel_invalid_channel():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    client_mock.send_message = AsyncMock(side_effect=ChannelInvalidError(request=None))
    
    res = await post_to_channel(client_mock, "Text")
    assert res is False

@pytest.mark.asyncio
async def test_post_to_channel_unexpected_error_retry_success():
    client_mock = MagicMock()
    client_mock.is_connected.return_value = True
    client_mock.send_message = AsyncMock(side_effect=[ValueError("Unexpected"), MagicMock()])
    
    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await post_to_channel(client_mock, "Text")
        assert res is True
        mock_sleep.assert_called_once_with(5)
