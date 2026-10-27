# tests/test_extra_dedup.py
import pytest
import sqlite3
import os
import time
from unittest.mock import patch, MagicMock
from database.dedup import (
    get_db_connection, is_processed, mark_as_processed, init_db
)
import database.dedup
import config

TEST_EXTRA_DB_PATH = "C:\\Users\\aioxtera01\\ai-news-bot\\database\\test_extra_posts.db"

@pytest.fixture(autouse=True)
def setup_extra_test_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", TEST_EXTRA_DB_PATH)
    monkeypatch.setattr(database.dedup.config, "DB_PATH", TEST_EXTRA_DB_PATH)
    
    if os.path.exists(TEST_EXTRA_DB_PATH):
        try:
            os.remove(TEST_EXTRA_DB_PATH)
        except PermissionError:
            pass
        for suffix in ["-wal", "-shm"]:
            path = TEST_EXTRA_DB_PATH + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except PermissionError:
                    pass
                
    init_db()
    yield
    if os.path.exists(TEST_EXTRA_DB_PATH):
        try:
            os.remove(TEST_EXTRA_DB_PATH)
        except PermissionError:
            pass
        for suffix in ["-wal", "-shm"]:
            path = TEST_EXTRA_DB_PATH + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except PermissionError:
                    pass

def test_db_connection_retry_and_fail():
    # We want to test when database is locked and we retry MAX_RETRIES times and then raise sqlite3.OperationalError
    # We can mock sqlite3.connect to raise OperationalError("database is locked")
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")) as mock_connect, \
         patch("time.sleep", MagicMock()) as mock_sleep:
         
         with pytest.raises(sqlite3.OperationalError):
             with get_db_connection():
                 pass
         
         assert mock_connect.call_count == 5 # MAX_RETRIES = 5
         assert mock_sleep.call_count == 5 # retries sleep 5 times (0, 1, 2, 3, 4)

def test_db_connection_other_operational_error():
    # An operational error that doesn't contain "locked" or "lock" should be raised immediately without retry
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("some other error")) as mock_connect:
         with pytest.raises(sqlite3.OperationalError):
             with get_db_connection():
                 pass
         assert mock_connect.call_count == 1

def test_db_connection_exception_rollback():
    # If an exception is raised inside the context manager, it should roll back and re-raise
    conn_mock = MagicMock()
    with patch("sqlite3.connect", return_value=conn_mock):
        with pytest.raises(ValueError):
            with get_db_connection() as conn:
                raise ValueError("Inside context manager error")
        conn_mock.rollback.assert_called_once()
        conn_mock.close.assert_called_once()

def test_get_content_hash_empty():
    from database.dedup import get_content_hash
    assert get_content_hash("") == ""
    assert get_content_hash(None) == ""
