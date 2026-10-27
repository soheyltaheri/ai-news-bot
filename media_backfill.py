# media_backfill.py
import os
import re
import sys
import asyncio
import argparse
from telethon import TelegramClient
from telethon.tl.types import Message
import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKFILL_SESSION_PATH = os.path.join(BASE_DIR, "sessions", "backfill_bot.session")

async def get_original_post_media(client, channel_username, message_id):
    """
    دریافت مدیا (عکس) از پست اصلی در کانال منبع
    """
    try:
        # دریافت پیام از کانال منبع
        entity = await client.get_input_entity(channel_username)
        messages = await client.get_messages(entity, ids=[int(message_id)])
        if messages and len(messages) > 0:
            msg = messages[0]
            if msg and msg.photo:
                # دانلود عکس به صورت موقت در حافظه یا فایل موقت
                temp_path = os.path.join(BASE_DIR, f"temp_{channel_username}_{message_id}.jpg")
                print(f"📥 در حال دانلود مدیا از @{channel_username}/{message_id}...")
                await client.download_media(msg.photo, file=temp_path)
                return temp_path
    except Exception as e:
        print(f"⚠️ خطا در واکشی پست اصلی @{channel_username}/{message_id}: {e}")
    return None

async def backfill_media(dry_run=False, limit=50):
    print("🚀 راه اندازی اسکریپت Media Backfill...")
    print(f"کانال مقصد: {config.DESTINATION_CHANNEL}")
    
    # ساخت کلاینت تلگرام با سشن مستقل برای جلوگیری از تداخل با بات اصلی
    client = TelegramClient(BACKFILL_SESSION_PATH, config.API_ID, config.API_HASH)
    await client.start()
    print("✅ با موفقیت به تلگرام متصل شد (سشن Backfill).")
    
    try:
        target_channel = config.DESTINATION_CHANNEL
        if not (target_channel.startswith("@") or target_channel.startswith("-100") or target_channel.isdigit()):
            target_channel = f"@{target_channel}"
            
        print(f"🔍 در حال خواندن {limit} پیام آخر از کانال {target_channel}...")
        entity = await client.get_input_entity(target_channel)
        
        # واکشی پیام‌ها از کانال مقصد
        messages = await client.get_messages(entity, limit=limit)
        
        total_checked = 0
        media_added = 0
        skipped = 0
        errors = 0
        
        for msg in messages:
            if not msg.text:
                continue
                
            total_checked += 1
            # بررسی اینکه آیا پیام عکس واقعی پیوست شده دارد یا فقط پیش‌نمایش لینک است
            # در تلثون، اگر پیام دارای عکس واقعی باشد، msg.photo پر است ولی msg.media نباید WebPage باشد
            has_real_photo = False
            if msg.photo:
                # چک کردن اینکه آیا مدیا از نوع پیش‌نمایش وب‌پیج (Link Preview) نیست
                from telethon.tl.types import MessageMediaWebPage
                if not isinstance(msg.media, MessageMediaWebPage):
                    has_real_photo = True
                    
            if has_real_photo:
                print(f"⏭️ پیام {msg.id} از قبل دارای عکس واقعی پیوست‌شده است. رد شد.")
                skipped += 1
                continue
                
            # استخراج لینک منبع با ریجکس
            # فرمت لینک: 🔗 [لینک اصلی](https://t.me/CHANNEL/MSG_ID)
            match = re.search(r'https://t\.me/([^/]+)/(\d+)', msg.text)
            if not match:
                print(f"⏭️ پیام {msg.id} لینک منبع معتبر تلگرامی ندارد. رد شد.")
                skipped += 1
                continue
                
            source_channel = match.group(1)
            source_msg_id = match.group(2)
            
            print(f"🔍 بررسی پست اصلی در کانال منبع: @{source_channel}/{source_msg_id} برای پیام {msg.id}...")
            
            # دانلود مدیا در صورت وجود
            temp_media_file = await get_original_post_media(client, source_channel, source_msg_id)
            
            if temp_media_file:
                if dry_run:
                    print(f"🔍 [حالت پیش‌نمایش] عکس پیدا شد برای پیام {msg.id}. هیچ تغییری اعمال نمی‌شود.")
                    media_added += 1
                    # حذف فایل موقت
                    if os.path.exists(temp_media_file):
                        os.remove(temp_media_file)
                else:
                    try:
                        print(f"🔄 در حال جایگزینی پیام {msg.id} با پیام دارای عکس...")
                        # ارسال پیام جدید با مدیا و همان کپشن
                        await client.send_file(
                            entity,
                            temp_media_file,
                            caption=msg.text,
                            parse_mode='md'
                        )
                        # حذف پیام متنی قدیمی بدون مدیا
                        await client.delete_messages(entity, [msg.id])
                        print(f"✅ پیام {msg.id} با موفقیت به روزرسانی و عکس به آن اضافه شد.")
                        media_added += 1
                    except Exception as ex:
                        print(f"❌ خطا در به روز رسانی پیام {msg.id}: {ex}")
                        errors += 1
                    finally:
                        # حذف فایل موقت عکس
                        if os.path.exists(temp_media_file):
                            os.remove(temp_media_file)
            else:
                print(f"⏭️ پیام اصلی عکس نداشت یا قابل دریافت نبود. رد شد.")
                skipped += 1
                
        print("\n================ REPORT ================")
        print(f"کل پیام‌های بررسی شده: {total_checked}")
        print(f"پیام‌های مجهز شده به مدیا: {media_added}")
        print(f"پیام‌های نادیده گرفته شده: {skipped}")
        print(f"خطاها: {errors}")
        print("========================================")
        
    finally:
        await client.disconnect()
        print("🔌 قطع ارتباط کلاینت با موفقیت انجام شد.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Media Backfill Script for Telegram AI News Bot")
    parser.add_argument("--dry-run", action="store_true", help="فقط بررسی می‌کند و هیچ تغییری در کانال اعمال نمی‌کند.")
    parser.add_argument("--limit", type=int, default=50, help="تعداد پیام‌هایی که از کانال مقصد بررسی می‌شوند.")
    args = parser.parse_args()
    
    asyncio.run(backfill_media(dry_run=args.dry_run, limit=args.limit))
