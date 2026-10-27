# config.py
import os
import sys
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Helpers to parse types
def get_env_int(key, default=None):
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"⚠️ Warning: Environment variable '{key}' value '{val}' is not an integer. Using default: {default}")
        return default

def get_env_list(key, default=None):
    val = os.environ.get(key)
    if not val:
        return default if default is not None else []
    return [item.strip() for item in val.split(",") if item.strip()]

# Telegram Configuration
API_ID = get_env_int("API_ID", 2040)
API_HASH = os.environ.get("API_HASH", "b18441a1ff607e10a989891a5462e627")
SESSION_NAME = os.environ.get("SESSION_NAME", "ai_news_bot")

# Paths setup - verify and create directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SESSION_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_PATH = os.path.join(SESSION_DIR, f"{SESSION_NAME}.session")

DB_DIR = os.path.join(BASE_DIR, "database")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "posts.db")

DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Target Channel
DESTINATION_CHANNEL = os.environ.get("DESTINATION_CHANNEL", "@ai_news_channel")

# AI Router / LLM Configuration
ROUTER_BASE_URL = os.environ.get("ROUTER_BASE_URL", "https://api.openai.com/v1")
ROUTER_API_KEY = os.environ.get("ROUTER_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")

# Source Configurations (comma-separated strings in env or default lists)
DEFAULT_RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence+OR+machine+learning+when:7d&hl=en-US&gl=US&ceid=US:en",
    # Community-recommended AI sources (Grok/X research pass 2026-07-17)
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://venturebeat.com/category/ai/feed/",
    "https://importai.substack.com/feed",
    "https://lastweekin.ai/feed",
    "https://www.latent.space/feed",
    "https://thesequence.substack.com/feed",
]
RSS_FEEDS = get_env_list("RSS_FEEDS")
if not RSS_FEEDS:
    RSS_FEEDS = DEFAULT_RSS_FEEDS

DEFAULT_SOURCE_CHANNELS = [
    # Original set
    "hiaimediaen",
    "Artificial_intelligence_in",
    "DeepLearning_ai",
    "machinelearningresearchnews",
    "MachineLearning9",
    "Machine_learn",
    "MLResearch",
    "AI_DeepLearning",
    "AIpersianChannel",
    "Hooshews",
    "HoosheMasnui",
    "PromptTG",
    "DeepLearning_fa",
    "irandeeplearning",
    "MeysamSafdary",
    "TechVid_TV",
    "Ai_NewsTv",
    "ainews_fa",
    # Verified additions from Grok/X community research (t.me preview OK)
    "TheRundownAI",
    "huggingface",
    "openai_news",
    "AnthropicAI",
    "MetaAI",
    "DeepSeek_AI",
    "perplexity_ai",
    "NousResearch",
    "stabilityai",
    "nvidiaai",
]
SOURCE_CHANNELS = get_env_list("SOURCE_CHANNELS")
if not SOURCE_CHANNELS:
    SOURCE_CHANNELS = DEFAULT_SOURCE_CHANNELS

DEFAULT_POST_INTERVAL = get_env_int("DEFAULT_POST_INTERVAL", 2)  # minutes
QUEUE_CAP = get_env_int("QUEUE_CAP", 45)
QUEUE_RESUME_THRESHOLD = get_env_int("QUEUE_RESUME_THRESHOLD", 20)

# Validation routine
def validate_config():
    errors = []
    if not API_ID:
        errors.append("API_ID is missing or not a valid integer.")
    if not API_HASH:
        errors.append("API_HASH is missing.")
    if not DESTINATION_CHANNEL:
        errors.append("DESTINATION_CHANNEL is missing.")
    elif not (DESTINATION_CHANNEL.startswith("@") or DESTINATION_CHANNEL.startswith("-100") or DESTINATION_CHANNEL.isdigit()):
        print(f"⚠️ Warning: DESTINATION_CHANNEL '{DESTINATION_CHANNEL}' may be in an invalid format (should start with @ or represent a channel ID).")
    
    if errors:
        print("❌ Configuration validation failed:")
        for error in errors:
            print(f"   - {error}")
        return False
    return True
