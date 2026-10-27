# database/dedup.py
import sqlite3
import os
import hashlib
import time
import contextlib
import logging
import config

logger = logging.getLogger(__name__)

# Constants for database retry / busy handling
MAX_RETRIES = 5
RETRY_DELAY = 0.5  # seconds
TIMEOUT = 30.0     # sqlite3 connect timeout

@contextlib.contextmanager
def get_db_connection():
    """
    Context manager for safely getting a SQLite connection with WAL mode enabled.
    Guarantees connection closing and transaction rollback on error.
    Handles SQLITE_BUSY / locked database exceptions with retries.
    """
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    
    conn = None
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            conn = sqlite3.connect(config.DB_PATH, timeout=TIMEOUT)
            # Ensure WAL mode is active
            conn.execute("PRAGMA journal_mode=WAL;")
            # Enable foreign key support just in case
            conn.execute("PRAGMA foreign_keys=ON;")
            # Optimize caching and synchronous mode for WAL
            conn.execute("PRAGMA synchronous=NORMAL;")
            break
        except sqlite3.OperationalError as e:
            last_error = e
            if "database is locked" in str(e) or "lock" in str(e).lower():
                logger.warning(f"Database locked. Retrying connection ({attempt + 1}/{MAX_RETRIES})...")
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass
                time.sleep(RETRY_DELAY * (2 ** attempt)) # exponential backoff
            else:
                raise e
    else:
        # If we couldn't connect after retries
        if last_error:
            logger.error(f"Failed to connect to database after {MAX_RETRIES} attempts: {last_error}")
            raise last_error
        else:
            raise sqlite3.OperationalError("Could not connect to database.")

    try:
        yield conn
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise e
    finally:
        try:
            conn.close()
        except Exception:
            pass

def init_db():
    """Initializes the database tables and configures WAL mode."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # processed_news table for deduplication
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_news (
                id TEXT PRIMARY KEY,
                content_hash TEXT,
                source TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Index on content_hash for fast deduplication checks
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_news_hash ON processed_news (content_hash);")
        
        # post_queue table for posting
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                body TEXT,
                media_url TEXT,
                source_url TEXT,
                source_name TEXT,
                rating INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending' -- pending, posted
            )
        """)
        # Index on status and created_at to speed up queue fetches
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_queue_status_created ON post_queue (status, created_at);")
        
        conn.commit()

def get_content_hash(text):
    """Generates a clean, normalized MD5 hash of text for deduplication."""
    if not text:
        return ""
    # Normalize text by removing all whitespaces and converting to lowercase
    normalized = "".join(text.split()).lower()
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def is_processed(news_id, text_content=None):
    """
    Checks if a news item has already been processed by ID or by content hash.
    Safe against sqlite3 errors and properly closes connection.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check by news ID first
        cursor.execute("SELECT 1 FROM processed_news WHERE id = ?", (news_id,))
        if cursor.fetchone():
            return True
            
        # Check by content hash if text is provided
        if text_content:
            content_hash = get_content_hash(text_content)
            cursor.execute("SELECT 1 FROM processed_news WHERE content_hash = ?", (content_hash,))
            if cursor.fetchone():
                return True
                
        return False

def mark_as_processed(news_id, source, text_content=None):
    """
    Marks a news item as processed in a transaction.
    Safely ignores duplicate inserts using SQLite's INSERT OR IGNORE or catching IntegrityError.
    """
    content_hash = get_content_hash(text_content) if text_content else None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO processed_news (id, content_hash, source) VALUES (?, ?, ?)",
                (news_id, content_hash, source)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Fallback handle if INSERT OR IGNORE doesn't catch it for some reason
            pass

def add_to_queue(title, body, media_url, source_url, source_name, rating):
    """Adds a drafted post to the drip queue."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO post_queue (title, body, media_url, source_url, source_name, rating)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (title, body, media_url, source_url, source_name, rating))
        conn.commit()

def get_next_pending_post():
    """Fetches the oldest pending post in the queue."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, body, media_url, source_url, source_name, rating 
            FROM post_queue 
            WHERE status = 'pending' 
            ORDER BY created_at ASC 
            LIMIT 1
        """)
        return cursor.fetchone()

def mark_post_as_posted(post_id):
    """Marks a post as successfully posted to prevent duplicate posting."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE post_queue SET status = 'posted' WHERE id = ?", (post_id,))
        conn.commit()

def get_pending_count():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM post_queue WHERE status = 'pending'")
        return cursor.fetchone()[0]

