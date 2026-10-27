# sources/telegram_announcements.py
import datetime
import logging
from telethon import TelegramClient, events
from database.dedup import is_processed, mark_as_processed, add_to_queue
from processor.ai_engine import process_news_with_ai

logger = logging.getLogger(__name__)

# Official/trusted Telegram channels for AI companies & open-source hubs
# Curated from Grok/X community recommendations + t.me/s preview verification.
# Dead/spam handles removed (e.g. cohere_ai, stabilityai_news, github_trending_ai).
ANNOUNCEMENT_CHANNELS = [
    "OpenAI_Updates",
    "openai_news",
    "huggingface",
    "DeepSeek_AI",
    "AnthropicAI",
    "MetaAI",
    "nvidiaai",
    "stabilityai",
    "perplexity_ai",
    "NousResearch",
    "TheRundownAI",
]

async def fetch_historical_announcements(client: TelegramClient, days=5):
    """
    Fetches historical messages from official announcement channels for the last `days` days.
    Sets higher rating (rating >= 4) for articles processed from these direct channels.
    """
    print(f"⏳ Fetching historical announcements from {len(ANNOUNCEMENT_CHANNELS)} channels...")
    time_limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    historical_items = []

    for channel in ANNOUNCEMENT_CHANNELS:
        try:
            print(f"🔍 Accessing channel: @{channel}")
            # Fetch up to 30 recent messages
            async for message in client.iter_messages(channel, limit=30):
                if message.date < time_limit:
                    break
                if not message.text or len(message.text) < 50:
                    continue
                
                unique_id = f"tg_ann_{message.chat_id}_{message.id}"
                if is_processed(unique_id, message.text):
                    continue

                media_url = None
                if getattr(message, 'photo', None):
                    media_url = f"https://t.me/{channel}/{message.id}"

                historical_items.append({
                    'id': unique_id,
                    'raw_text': message.text,
                    'url': f"https://t.me/{channel}/{message.id}" if getattr(message.chat, 'username', None) else "https://t.me",
                    'source_name': getattr(message.chat, 'title', channel),
                    'media_url': media_url,
                    'date': message.date,
                    'is_official_announcement': True
                })
        except Exception as e:
            print(f"❌ Error reading historical feed for @{channel}: {e}")
            
    # Sort from oldest to newest
    historical_items.sort(key=lambda x: x['date'])
    return historical_items

async def start_announcements_monitoring(client: TelegramClient):
    """
    Starts live monitoring of official announcement channels.
    Incoming messages are automatically prioritized and given rating >= 4.
    """
    print(f"📡 Live monitoring of official AI announcement channels active.")
    
    @client.on(events.NewMessage(chats=ANNOUNCEMENT_CHANNELS))
    async def new_announcement_handler(event):
        message = event.message
        if not message.text or len(message.text) < 50:
            return
            
        unique_id = f"tg_ann_{message.chat_id}_{message.id}"
        if is_processed(unique_id, message.text):
            return
            
        print(f"📥 New live announcement from @{event.chat.username or event.chat.title} received. Processing...")
        
        # Process using AI
        ai_result = await process_news_with_ai(message.text, event.chat.title)
        
        if ai_result and ai_result.get('is_relevant', False):
            # Prioritize: raise rating to at least 4 if it's less
            rating = ai_result.get('rating', 4)
            if rating < 4:
                rating = 4
                
            source_url = f"https://t.me/{event.chat.username}/{message.id}" if event.chat.username else "https://t.me"
            
            media_url = None
            if getattr(message, 'photo', None):
                channel_identifier = event.chat.username if event.chat.username else str(message.chat_id)
                media_url = f"https://t.me/{channel_identifier}/{message.id}"
            
            # Save category marker in the body
            category = ai_result.get('category', '🔥 تکنولوژی')
            body_with_category = f"[CATEGORY_MARKER: {category}] {ai_result.get('body', '')}"

            add_to_queue(
                title=ai_result.get('title'),
                body=body_with_category,
                media_url=media_url,
                source_url=source_url,
                source_name=event.chat.title,
                rating=rating
            )
            print(f"➕ Announcement added to queue with rating {rating}: {ai_result.get('title')}")
            mark_as_processed(unique_id, event.chat.title, message.text)
        else:
            print("⏭️ Live announcement deemed not relevant by AI, discarded.")
            mark_as_processed(unique_id, event.chat.title, message.text)
