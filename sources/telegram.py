# sources/telegram.py
from telethon import TelegramClient, events
from telethon.errors import RPCError, FloodWaitError
from database.dedup import is_processed, mark_as_processed, add_to_queue
from processor.ai_engine import process_news_with_ai
from config import SOURCE_CHANNELS
import datetime
import asyncio
import re

async def check_channel_join_or_preview(client: TelegramClient, channel: str) -> bool:
    """
    Checks channel join status or checks if it is accessible.
    If the client is not in the channel and it is public, it returns True.
    If private and client doesn't have access, returns False.
    """
    try:
        # Check if we can get entity (implies access/join status)
        entity = await client.get_entity(channel)
        return True
    except Exception as e:
        print(f"⚠️ Cannot get entity for @{channel}: {e}. Checking public preview web accessibility...")
        # Fallback to web preview check where possible
        from sources.enhanced_sources import verify_telegram_channel_preview
        try:
            is_public_and_active = await verify_telegram_channel_preview(channel)
            if is_public_and_active:
                print(f"ℹ️ Channel @{channel} appears to be public (accessible via preview).")
                return True
        except Exception as pe:
            print(f"⚠️ Preview check failed for @{channel}: {pe}")
        return False

def get_channel_and_post_url(channel, message) -> tuple:
    """
    Determines source URL and media URL for a given message.
    Ensures that if there is any image, we set media_url to the public/direct t.me link.
    """
    channel_name = getattr(message.chat, 'username', None)
    if not channel_name:
        # Fallback if channel name not in chat
        if isinstance(channel, str):
            channel_name = channel.replace('@', '').strip()
        else:
            channel_name = str(getattr(message.chat, 'id', ''))
    
    # Clean channel name
    channel_name = channel_name.replace('https://t.me/', '')
    
    source_url = f"https://t.me/{channel_name}/{message.id}"
    
    # We check if there is an image (photo, media group, or document image)
    has_image = False
    
    # 1. Single photo check
    if getattr(message, 'photo', None):
        has_image = True
    # 2. Document check (check if it's an image document)
    elif getattr(message, 'document', None):
        mime = getattr(message.document, 'mime_type', '') or ''
        if mime.startswith('image/'):
            has_image = True
    # 3. Media group check
    elif getattr(message, 'grouped_id', None) is not None:
        # It's part of a media group
        has_image = True
        
    media_url = source_url if has_image else None
    return source_url, media_url

async def fetch_historical_telegram_posts(client: TelegramClient, days=5):
    """
    در اولین اجرا، پست‌های ۵ روز گذشته از کانال‌های هدف را می‌خواند
    """
    print(f"⏳ در حال واکشی پست‌های ۵ روز اخیر از {len(SOURCE_CHANNELS)} کانال تلگرام...")
    time_limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    historical_items = []

    # Map grouped_id -> message mapping to handle albums correctly
    grouped_messages = {}

    for channel in SOURCE_CHANNELS:
        # Verify access/join status before attempting to fetch
        has_access = await check_channel_join_or_preview(client, channel)
        if not has_access:
            print(f"❌ عدم دسترسی یا نامعتبر بودن کانال @{channel}. واکشی آرشیو متوقف شد.")
            continue

        retries = 3
        delay = 5
        for attempt in range(1, retries + 1):
            try:
                print(f"🔍 بررسی آرشیو کانال: @{channel}")
                # گرفتن تا سقف 30 پیام اخیر برای هر کانال
                async for message in client.iter_messages(channel, limit=30):
                    # پیام باید در محدوده ۵ روز اخیر باشد
                    if message.date < time_limit:
                        break
                    
                    # Handle media groups (albums)
                    # For albums, we group messages by grouped_id.
                    # Usually, the text is attached to the first message of the group,
                    # but we should ensure we get the text from the group and keep the main message.
                    if getattr(message, 'grouped_id', None) is not None:
                        gid = message.grouped_id
                        if gid not in grouped_messages:
                            grouped_messages[gid] = []
                        grouped_messages[gid].append(message)
                        # We will process grouped messages after iterating, or if we have already found the main one.
                        # For now, collect them. We continue to make sure we don't miss non-grouped messages or other parts.
                    
                    if not message.text or len(message.text) < 50:
                        if getattr(message, 'grouped_id', None) is None:
                            continue
                    
                    # If it's a standalone message (no media group)
                    if getattr(message, 'grouped_id', None) is None:
                        unique_id = f"tg_{message.chat_id}_{message.id}"
                        if is_processed(unique_id, message.text):
                            continue

                        source_url, media_url = get_channel_and_post_url(channel, message)

                        historical_items.append({
                            'id': unique_id,
                            'raw_text': message.text,
                            'url': source_url,
                            'source_name': getattr(message.chat, 'title', channel),
                            'media_url': media_url,
                            'date': message.date
                        })
                break # Break retry loop on success
            except (RPCError, asyncio.TimeoutError) as e:
                print(f"⚠️ خطای ارتباطی Telethon در خواندن آرشیو کانال @{channel} (تلاش {attempt}/{retries}): {e}")
                if attempt == retries:
                    print(f"❌ خطا پس از حداکثر تلاش‌ها برای کانال @{channel}")
                else:
                    await asyncio.sleep(delay)
                    delay *= 2
            except Exception as e:
                print(f"❌ خطا در خواندن آرشیو کانال @{channel}: {e}")
                break

    # Now handle gathered media groups (albums)
    # For each group, we find the message containing the text (usually the first one, or we combine them).
    # We choose the message with the text as the primary message, but set its media_url to point to it.
    for gid, msgs in grouped_messages.items():
        # Sort by message ID ascending so we get them in chronological order
        msgs.sort(key=lambda m: m.id)
        
        # Find message with text
        main_message = None
        for m in msgs:
            if m.text and len(m.text) >= 50:
                main_message = m
                break
        
        if not main_message:
            # Fallback to the first message if none has long enough text
            main_message = msgs[0]
            
        if not main_message.text or len(main_message.text) < 50:
            continue
            
        unique_id = f"tg_{main_message.chat_id}_{main_message.id}"
        if is_processed(unique_id, main_message.text):
            continue

        # Since it is a media group containing images, we set media_url (using the main message URL)
        source_url, media_url = get_channel_and_post_url(msgs[0].chat.username or str(main_message.chat_id), main_message)
        
        # Override media_url to ensure it is set because this is a media group
        media_url = source_url

        historical_items.append({
            'id': unique_id,
            'raw_text': main_message.text,
            'url': source_url,
            'source_name': getattr(main_message.chat, 'title', str(main_message.chat_id)),
            'media_url': media_url,
            'date': main_message.date
        })
            
    # مرتب‌سازی بر اساس تاریخ قدیمی به جدید
    historical_items.sort(key=lambda x: x['date'])
    return historical_items

async def start_telegram_monitoring(client: TelegramClient):
    """
    شروع مانیتورینگ لایو کانال‌ها
    """
    print(f"📡 مانیتورینگ لایو کانال‌های تلگرام فعال شد.")
    
    # Store processed media group IDs to avoid duplicate processing in live monitoring
    processed_groups = set()
    
    # We will verify all monitored channels first to warn about access issues
    for channel in SOURCE_CHANNELS:
        has_access = await check_channel_join_or_preview(client, channel)
        if not has_access:
            print(f"⚠️ هشدار: ممکن است کلاینت به کانال @{channel} دسترسی نداشته باشد.")

    @client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def new_message_handler(event):
        try:
            message = event.message
            
            # Handle media groups (albums) in live monitoring
            if getattr(message, 'grouped_id', None) is not None:
                gid = message.grouped_id
                if gid in processed_groups:
                    # Already handled this media group (album)
                    return
                
                # Fetch the whole media group
                # Live messages in media group might arrive close to each other.
                # Wait briefly to let other messages in the group arrive, then fetch the group.
                await asyncio.sleep(1.5)
                try:
                    # In Telethon, we can fetch messages of the same group or get all messages from the conversation.
                    # A robust way is to fetch the latest messages and filter by grouped_id.
                    group_messages = []
                    async for msg in client.iter_messages(event.chat_id, limit=10):
                        if getattr(msg, 'grouped_id', None) == gid:
                            group_messages.append(msg)
                    
                    if group_messages:
                        # Sort by id ascending
                        group_messages.sort(key=lambda m: m.id)
                        
                        # Find the one with text
                        main_msg = None
                        for m in group_messages:
                            if m.text and len(m.text) >= 50:
                                main_msg = m
                                break
                        if not main_msg:
                            main_msg = group_messages[0]
                            
                        if not main_msg.text or len(main_msg.text) < 50:
                            processed_groups.add(gid)
                            return
                            
                        message = main_msg
                        processed_groups.add(gid)
                except Exception as ge:
                    print(f"⚠️ خطا در واکشی آلبوم رسانه‌ای: {ge}. با پیام فعلی ادامه می‌دهیم.")
            
            if not message.text or len(message.text) < 50:
                return
                
            unique_id = f"tg_{message.chat_id}_{message.id}"
            if is_processed(unique_id, message.text):
                return
                
            print(f"📥 پیام لایو جدید از کانال {event.chat.title} دریافت شد. ارسال به پردازش هوش مصنوعی...")
            
            # پردازش مستقیم با AI (MiMo v2.5) بدون فیلتر دو مرحله‌ای
            ai_result = await process_news_with_ai(message.text, event.chat.title)
            
            if ai_result and ai_result.get('is_relevant', False):
                if ai_result.get('rating', 0) >= 3:
                    channel_identifier = event.chat.username if event.chat.username else str(message.chat_id)
                    source_url, media_url = get_channel_and_post_url(channel_identifier, message)
                    
                    # For albums, ensure media_url is set
                    if getattr(message, 'grouped_id', None) is not None:
                        media_url = source_url
                    
                    # اضافه کردن مستقیم به صف پست‌ها
                    add_to_queue(
                        title=ai_result.get('title'),
                        body=ai_result.get('body'),
                        media_url=media_url,
                        source_url=source_url,
                        source_name=event.chat.title,
                        rating=ai_result.get('rating')
                    )
                    print(f"➕ پیام لایو با موفقیت به صف ارسال اضافه شد: {ai_result.get('title')}")
                    mark_as_processed(unique_id, event.chat.title, message.text)
                else:
                    print(f"⏭️ خبر رتبه پایینی داشت ({ai_result.get('rating')})، رد شد.")
                    mark_as_processed(unique_id, event.chat.title, message.text)
            else:
                print("⏭️ خبر نامرتبط تشخیص داده شد، رد شد.")
                mark_as_processed(unique_id, event.chat.title, message.text)
        except Exception as e:
            print(f"❌ خطا در پردازش پیام لایو: {e}")
