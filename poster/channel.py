import os
import asyncio
import re
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    RPCError, 
    ServerError, 
    AuthKeyDuplicatedError,
    ChannelInvalidError,
    ChatAdminRequiredError
)
from config import DESTINATION_CHANNEL

def format_post(ai_data, source_url, source_name):
    """
    Formats the post to be posted in the destination channel.
    Sanitizes values to avoid crashes.
    Supports parsing dynamic categories embedded in the body.
    """
    if not ai_data:
        ai_data = {}
        
    rating = ai_data.get('rating', 3)
    try:
        rating_val = int(rating)
    except (ValueError, TypeError):
        rating_val = 3
        
    rating_stars = "⭐" * max(1, min(5, rating_val))
    
    title = ai_data.get('title', 'خبر جدید').strip()
    raw_body = ai_data.get('body', '').strip()
    
    # Extract category if embedded as [CATEGORY_MARKER: category_name] at start of body
    category = "🔥 تکنولوژی"
    body = raw_body
    
    marker_match = re.match(r"^\[CATEGORY_MARKER:\s*(.*?)\]\s*(.*)$", raw_body, re.DOTALL)
    if marker_match:
        category = marker_match.group(1).strip()
        body = marker_match.group(2).strip()
    elif ai_data.get('category'):
        # Fallback if category was directly passed (e.g. in tests)
        category = ai_data.get('category').strip()
        
    source_name = str(source_name).strip()
    source_url = str(source_url).strip()
    
    post = f"{category} | AI News Update 🚀\n\n"
    post += f"📌 **{title}**\n\n"
    if body:
        post += f"📝 **تحلیل و خلاصه خبر:**\n{body}\n\n"
    post += f"{rating_stars} | 🌐 منبع: {source_name}\n"
    if source_url and source_url.startswith("http"):
        post += f"🔗 [لینک مستقیم]({source_url})\n\n"
    else:
        post += f"🔗 منبع بدون لینک\n\n"
    post += f"━━━━━━━━━━━━━━━━━\n"
    post += f"📢 {DESTINATION_CHANNEL}"
    return post

async def post_to_channel(client: TelegramClient, formatted_text, media_url=None, max_retries=3):
    """
    Sends the formatted message to the target channel with safety checks,
    connection loss handling, specific Telethon error catches, and retry backoff.
    """
    if not client:
        print("❌ Telegram client is None or not initialized.")
        return False

    # Check client connectivity, reconnect if not connected
    try:
        if not client.is_connected():
            print("⚠️ Client not connected, attempting to connect...")
            await client.connect()
    except Exception as ec:
        print(f"❌ Failed to ensure client connection: {ec}")
        return False

    retry_delay = 5
    for attempt in range(1, max_retries + 1):
        try:
            if media_url:
                try:
                    await client.send_file(
                        DESTINATION_CHANNEL, 
                        media_url, 
                        caption=formatted_text, 
                        parse_mode='md'
                    )
                except Exception as media_err:
                    print(f"⚠️ Failed to send with media (attempt {attempt}/{max_retries}): {media_err}. Falling back to text only.")
                    await client.send_message(
                        DESTINATION_CHANNEL, 
                        formatted_text, 
                        parse_mode='md', 
                        link_preview=False
                    )
            else:
                await client.send_message(
                    DESTINATION_CHANNEL, 
                    formatted_text, 
                    parse_mode='md', 
                    link_preview=False
                )
            
            print(f"✅ Post successfully sent to channel {DESTINATION_CHANNEL}.")
            return True

        except FloodWaitError as e:
            print(f"⚠️ Flood wait error: Must wait {e.seconds} seconds. Retrying after sleep...")
            if e.seconds > 300:
                print("❌ Flood wait too long (> 5 minutes). Aborting this post.")
                return False
            await asyncio.sleep(e.seconds)
            
        except (ServerError, RPCError) as e:
            print(f"⚠️ Telethon RPC/Server error (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                print("❌ Max retries reached for server/RPC error.")
                return False
            await asyncio.sleep(retry_delay)
            retry_delay *= 2
            
        except (ChannelInvalidError, ChatAdminRequiredError) as e:
            print(f"❌ Channel permissions or existence error: {e}. Confirm DESTINATION_CHANNEL is correct and client has posting rights.")
            return False # Non-retriable error
            
        except Exception as e:
            print(f"❌ Unexpected error in post_to_channel (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                return False
            await asyncio.sleep(retry_delay)
            retry_delay *= 2

    return False
