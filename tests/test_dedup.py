# tests/test_dedup.py
import os
import shutil
import sqlite3
import pytest
from database.dedup import (
    init_db, get_content_hash, is_processed, mark_as_processed,
    add_to_queue, get_next_pending_post, mark_post_as_posted
)
import database.dedup
import config

# Use a test database path
TEST_DB_PATH = "C:\\Users\\aioxtera01\\ai-news-bot\\database\\test_posts.db"

@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    # Override both config.DB_PATH and database.dedup.config.DB_PATH
    monkeypatch.setattr(config, "DB_PATH", TEST_DB_PATH)
    monkeypatch.setattr(database.dedup.config, "DB_PATH", TEST_DB_PATH)
    
    # Remove any existing test database
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass
        # Also clean up WAL/SHM files if any
        for suffix in ["-wal", "-shm"]:
            path = TEST_DB_PATH + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except PermissionError:
                    pass
                
    init_db()
    yield
    # Clean up after test
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass
        for suffix in ["-wal", "-shm"]:
            path = TEST_DB_PATH + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except PermissionError:
                    pass

def test_init_db():
    assert os.path.exists(TEST_DB_PATH)
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    
    # Check tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    assert "processed_news" in tables
    assert "post_queue" in tables
    
    # Check WAL mode
    cursor.execute("PRAGMA journal_mode;")
    mode = cursor.fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()

def test_content_hash():
    text1 = "This is a test message. "
    text2 = "this is a test message."
    text3 = "This  is\na test\tmessage."
    
    hash1 = get_content_hash(text1)
    hash2 = get_content_hash(text2)
    hash3 = get_content_hash(text3)
    
    assert hash1 == hash2
    assert hash2 == hash3

def test_processed_news():
    news_id = "test_1"
    text = "Important AI developments are occurring."
    
    assert not is_processed(news_id, text)
    
    mark_as_processed(news_id, "test_source", text)
    assert is_processed(news_id)
    assert is_processed("other_id", text) # should match by hash
    
    # Check duplicate insert doesn't raise exception
    mark_as_processed(news_id, "test_source", text)

def test_queue_operations():
    add_to_queue(
        title="AI News",
        body="Body of the news",
        media_url="http://example.com/image.jpg",
        source_url="http://example.com/news",
        source_name="HN",
        rating=4
    )
    
    post = get_next_pending_post()
    assert post is not None
    post_id, title, body, media_url, source_url, source_name, rating = post
    assert title == "AI News"
    assert body == "Body of the news"
    assert media_url == "http://example.com/image.jpg"
    assert source_url == "http://example.com/news"
    assert source_name == "HN"
    assert rating == 4
    
    mark_post_as_posted(post_id)
    
    post2 = get_next_pending_post()
    assert post2 is None
