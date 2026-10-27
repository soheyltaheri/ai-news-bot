import asyncio
import sys
import signal
import math
import random
from telethon import TelegramClient
from config import (
    API_ID, API_HASH, SESSION_PATH, RSS_FEEDS, validate_config,
    DEFAULT_POST_INTERVAL as CONFIG_DEFAULT_POST_INTERVAL,
    QUEUE_CAP as CONFIG_QUEUE_CAP,
    QUEUE_RESUME_THRESHOLD as CONFIG_QUEUE_RESUME_THRESHOLD
)
from database.dedup import (
    init_db, mark_as_processed, is_processed, 
    add_to_queue, get_next_pending_post, mark_post_as_posted,
    get_pending_count
)

from sources.web_sources import fetch_hacker_news_top, fetch_rss_feeds
from sources.telegram import fetch_historical_telegram_posts, start_telegram_monitoring
from sources.telegram_announcements import fetch_historical_announcements, start_announcements_monitoring
from sources.company_feeds import fetch_company_news
from sources.enhanced_sources import fetch_from_reddit_subreddit, fetch_github_trending_ai
from processor.ai_engine import process_news_with_ai
from poster.channel import format_post, post_to_channel

# Global stop event to coordinate shutdown across all async tasks
shutdown_event = asyncio.Event()
extraction_paused = asyncio.Event()

# User configurable parameters
DEFAULT_POST_INTERVAL = CONFIG_DEFAULT_POST_INTERVAL
QUEUE_CAP = CONFIG_QUEUE_CAP
QUEUE_RESUME_THRESHOLD = CONFIG_QUEUE_RESUME_THRESHOLD
BOOTSTRAP_ENABLED = True
BOOTSTRAP_DAYS = 5
LIVE_MONITORING_ENABLED = True
WEB_FETCHING_ENABLED = True

def ask_input(prompt, default_val):
    try:
        val = input(prompt).strip()
        if not val:
            return default_val
        return val
    except (EOFError, KeyboardInterrupt, OSError):
        return default_val

def ask_int(prompt, default_val):
    val = ask_input(prompt, str(default_val))
    try:
        return int(val)
    except ValueError:
        print(f"Invalid integer. Using default: {default_val}")
        return default_val

def ask_bool(prompt, default_val):
    default_str = 'y' if default_val else 'n'
    val = ask_input(f"{prompt} (default '{default_str}'): ", default_str).lower()
    return val.startswith('y')

def interactive_config_setup():
    global DEFAULT_POST_INTERVAL, QUEUE_CAP, QUEUE_RESUME_THRESHOLD
    global BOOTSTRAP_ENABLED, BOOTSTRAP_DAYS, LIVE_MONITORING_ENABLED, WEB_FETCHING_ENABLED
    
    print("🛠️ Entering interactive configuration setup...")
    
    DEFAULT_POST_INTERVAL = ask_int(f"DEFAULT_POST_INTERVAL (default {DEFAULT_POST_INTERVAL} minutes): ", DEFAULT_POST_INTERVAL)
    QUEUE_CAP = ask_int(f"QUEUE_CAP (default {QUEUE_CAP}): ", QUEUE_CAP)
    QUEUE_RESUME_THRESHOLD = ask_int(f"QUEUE_RESUME_THRESHOLD (default {QUEUE_RESUME_THRESHOLD}): ", QUEUE_RESUME_THRESHOLD)
    
    BOOTSTRAP_ENABLED = ask_bool("Bootstrap (historical archive) enabled? (y/n)", BOOTSTRAP_ENABLED)
    if BOOTSTRAP_ENABLED:
        BOOTSTRAP_DAYS = ask_int(f"Bootstrap days to fetch (default {BOOTSTRAP_DAYS}): ", BOOTSTRAP_DAYS)
        
    LIVE_MONITORING_ENABLED = ask_bool("Live Telegram channel monitoring enabled? (y/n)", LIVE_MONITORING_ENABLED)
    WEB_FETCHING_ENABLED = ask_bool("Web fetching (RSS/HackerNews) enabled? (y/n)", WEB_FETCHING_ENABLED)
    
    print("\nConfiguration applied:")
    print(f" - DEFAULT_POST_INTERVAL: {DEFAULT_POST_INTERVAL}")
    print(f" - QUEUE_CAP: {QUEUE_CAP}")
    print(f" - QUEUE_RESUME_THRESHOLD: {QUEUE_RESUME_THRESHOLD}")
    print(f" - BOOTSTRAP_ENABLED: {BOOTSTRAP_ENABLED}")
    if BOOTSTRAP_ENABLED:
        print(f" - BOOTSTRAP_DAYS: {BOOTSTRAP_DAYS}")
    print(f" - LIVE_MONITORING_ENABLED: {LIVE_MONITORING_ENABLED}")
    print(f" - WEB_FETCHING_ENABLED: {WEB_FETCHING_ENABLED}\n")

# Apply monkeypatch to pause live telegram monitoring when extraction is paused
import sources.telegram

original_process_news_with_ai = sources.telegram.process_news_with_ai

async def wrapped_process_news_with_ai(text, source_name):
    while extraction_paused.is_set() and not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    return await original_process_news_with_ai(text, source_name)

sources.telegram.process_news_with_ai = wrapped_process_news_with_ai


def get_smart_delay(default_interval_minutes, queue_size, queue_cap=45, resume_threshold=20):
    if queue_size >= queue_cap:
        scale = min(1.0, math.log2(max(2, default_interval_minutes) / 2) / math.log2(15))
        min_mult = 0.70 - 0.35 * scale
        max_mult = 0.90 - 0.30 * scale
        delay = default_interval_minutes * random.uniform(min_mult, max_mult)
        return max(1.0, delay)  # minimum 1 minute
    return default_interval_minutes


async def process_raw_item(item):
    """
    بررسی اولیه، پردازش مستقیم با AI و ریختن پست تایید شده در صف (Queue)
    """
    if shutdown_event.is_set():
        return
        
    try:
        # Check if already processed
        if is_processed(item['id'], item['raw_text']):
            return
            
        print(f"🔄 پردازش خبر جدید از: {item['source_name']}")
        
        # پردازش مستقیم با AI (MiMo v2.5) بدون فیلتر دو مرحله‌ای
        ai_result = await process_news_with_ai(item['raw_text'], item['source_name'])
        
        if not ai_result:
            mark_as_processed(item['id'], item['source_name'], item['raw_text'])
            return
            
        if not ai_result.get('is_relevant', False):
            print("⏭️ خبر نامرتبط تشخیص داده شد، رد شد.")
            mark_as_processed(item['id'], item['source_name'], item['raw_text'])
            return
            
        # Official channels automatically get rating >= 4
        is_official = item.get('is_official_announcement', False)
        
        if not is_official and ai_result.get('rating', 0) < 3:
            print(f"⏭️ خبر رتبه پایینی داشت ({ai_result.get('rating')})، رد شد.")
            mark_as_processed(item['id'], item['source_name'], item['raw_text'])
            return
            
        rating = ai_result.get('rating', 3)
        if is_official and rating < 4:
            rating = 4

        # ذخیره دسته‌بندی در ابتدای فیلد body با استفاده از مارکر مخصوص
        category = ai_result.get('category', '🔥 تکنولوژی')
        body_with_category = f"[CATEGORY_MARKER: {category}] {ai_result.get('body', '')}"

        # اضافه کردن مستقیم خبر تایید شده به صف ارسال دیتابیس
        add_to_queue(
            title=ai_result.get('title'),
            body=body_with_category,
            media_url=item.get('media_url'),
            source_url=item.get('url'),
            source_name=item.get('source_name'),
            rating=rating
        )
        print(f"➕ خبر با موفقیت به صف ارسال اضافه شد: {ai_result.get('title')} (امتیاز: {rating})")
        mark_as_processed(item['id'], item['source_name'], item['raw_text'])
    except Exception as e:
        print(f"❌ خطا در پردازش آیتم خام ({item.get('id')}): {e}")


async def queue_sender_worker(client):
    """
    پروسه پس‌زمینه که صف ارسال را چک می‌کند و هر ۲ دقیقه یک پست می‌فرستد.
    شامل مدیریت قطع ارتباط تلگرام و خطاها با امکان خاموشی تمیز.
    """
    print("🤖 کارگر صف ارسال فعال شد.")
    while not shutdown_event.is_set():
        try:
            # Check queue size and update extraction_paused
            current_size = get_pending_count()
            if current_size >= QUEUE_CAP:
                if not extraction_paused.is_set():
                    extraction_paused.set()
                    print(f"⚠️ ظرفیت صف به {current_size} رسید (حداکثر {QUEUE_CAP}). فرآیند استخراج موقتاً متوقف شد.")
            elif current_size <= QUEUE_RESUME_THRESHOLD:
                if extraction_paused.is_set():
                    extraction_paused.clear()
                    print(f"🔄 ظرفیت صف به {current_size} کاهش یافت (حد مجاز {QUEUE_RESUME_THRESHOLD}). فرآیند استخراج از سر گرفته شد.")

            post = get_next_pending_post()
            if post:
                post_id, title, body, media_url, source_url, source_name, rating = post
                
                ai_data = {
                    'title': title,
                    'body': body,
                    'rating': rating
                }
                
                formatted_post = format_post(ai_data, source_url, source_name)
                print(f"📤 ارسال پست از صف: {title}")
                
                # بررسی اتصال قبل از ارسال
                if not client.is_connected():
                    print("⚠️ تلگرام قطع شده است. تلاش برای اتصال مجدد...")
                    await client.connect()
                
                # ارسال به کانال تلگرام
                success = await post_to_channel(client, formatted_post, media_url)
                if success:
                    mark_post_as_posted(post_id)
                    
                    # Update queue size and pause/resume event state after posting
                    current_size = get_pending_count()
                    if current_size >= QUEUE_CAP:
                        if not extraction_paused.is_set():
                            extraction_paused.set()
                            print(f"⚠️ ظرفیت صف به {current_size} رسید (حداکثر {QUEUE_CAP}). فرآیند استخراج موقتاً متوقف شد.")
                    elif current_size <= QUEUE_RESUME_THRESHOLD:
                        if extraction_paused.is_set():
                            extraction_paused.clear()
                            print(f"🔄 ظرفیت صف به {current_size} کاهش یافت (حد مجاز {QUEUE_RESUME_THRESHOLD}). فرآیند استخراج از سر گرفته شد.")
                    
                    # Compute delay
                    effective_size = current_size if not extraction_paused.is_set() else max(current_size, QUEUE_CAP)
                    delay_minutes = get_smart_delay(DEFAULT_POST_INTERVAL, effective_size, QUEUE_CAP, QUEUE_RESUME_THRESHOLD)
                    delay_seconds = delay_minutes * 60
                    
                    if extraction_paused.is_set():
                        print(f"⏳ وقفه هوشمند {delay_minutes:.2f} دقیقه‌ای (بر اساس صف {current_size} تایی) تا پست بعدی...")
                    else:
                        print(f"⏳ وقفه {delay_minutes:.2f} دقیقه‌ای برای پست بعدی در صف...")
                    
                    # وقفه که نسبت به رویداد shutdown حساس است
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=delay_seconds)
                    except asyncio.TimeoutError:
                        pass # ادامه حلقه
                else:
                    print("❌ خطا در ارسال پست، مجدداً تلاش خواهد شد...")
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass
            else:
                # اگر صف خالی بود، ۳۰ ثانیه صبر کن و دوباره چک کن
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            print("🛑 کارگر صف ارسال لغو شد.")
            break
        except Exception as e:
            print(f"❌ خطا در کارگر صف: {e}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass


async def web_fetching_loop():
    """
    حلقه دریافت اخبار وب (RSS & Hacker News) هر ۵ دقیقه یک‌بار به طور مجزا از بقیه سرویس‌ها
    """
    if not WEB_FETCHING_ENABLED:
        print("🌐 حلقه دریافت اخبار وب غیرفعال است.")
        return

    print("🌐 حلقه دریافت اخبار وب فعال شد.")
    while not shutdown_event.is_set():
        try:
            # Check extraction_paused at start of loop
            while extraction_paused.is_set() and not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            
            if shutdown_event.is_set():
                break

            print("\n🔄 شروع دور جدید واکشی وب (RSS & Hacker News)...")
            
            # Hacker News
            hn_items = await fetch_hacker_news_top()
            for item in hn_items:
                if shutdown_event.is_set():
                    break
                while extraction_paused.is_set() and not shutdown_event.is_set():
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                await process_raw_item(item)
                
            # Google News RSS
            if not shutdown_event.is_set():
                rss_items = await fetch_rss_feeds(RSS_FEEDS)
                for item in rss_items:
                    if shutdown_event.is_set():
                        break
                    while extraction_paused.is_set() and not shutdown_event.is_set():
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                    await process_raw_item(item)
                    
            # AI Company Blogs and News
            if not shutdown_event.is_set():
                print("🔄 شروع واکشی اخبار از سایت‌های رسمی شرکت‌های هوش مصنوعی...")
                company_items = await fetch_company_news()
                for item in company_items:
                    if shutdown_event.is_set():
                        break
                    while extraction_paused.is_set() and not shutdown_event.is_set():
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                    await process_raw_item(item)
                    
            # Reddit Subreddits (expanded from Grok/X community research)
            if not shutdown_event.is_set():
                for sub in [
                    'MachineLearning', 'artificial', 'LocalLLaMA', 'OpenAI',
                    'singularity', 'ClaudeAI', 'MachineLearningNews',
                ]:
                    if shutdown_event.is_set():
                        break
                    print(f"🔄 شروع واکشی اخبار از ردیت r/{sub}...")
                    reddit_items = await fetch_from_reddit_subreddit(sub, limit=8)
                    for item in reddit_items:
                        if shutdown_event.is_set():
                            break
                        while extraction_paused.is_set() and not shutdown_event.is_set():
                            try:
                                await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                            except asyncio.TimeoutError:
                                pass
                        await process_raw_item(item)
                    
            # GitHub Trending AI
            if not shutdown_event.is_set():
                print("🔄 شروع واکشی پروژه های ترند گیت هاب (GitHub Trending)...")
                trending_items = await fetch_github_trending_ai(limit=10)
                for item in trending_items:
                    if shutdown_event.is_set():
                        break
                    while extraction_paused.is_set() and not shutdown_event.is_set():
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                    await process_raw_item(item)
                    
            print("💤 پایان دور وب. ۵ دقیقه خواب...")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            print("🛑 حلقه دریافت اخبار وب لغو شد.")
            break
        except Exception as e:
            print(f"❌ خطا در حلقه دریافت اخبار وب: {e}")
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass


async def main():
    interactive_config_setup()

    if not validate_config():
        sys.exit(1)

    print("🚀 در حال راه‌اندازی AI News Bot...")
    try:
        init_db()
    except Exception as e:
        print(f"❌ خطا در مقداردهی اولیه دیتابیس: {e}")
        sys.exit(1)
    
    # لاگین به کلاینت تلگرام با استفاده از سشن احراز شده
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    
    # تنظیم سیگنال‌های خاموش کردن تمیز (فقط در سیستم‌های یونیکس یا سازگار. در ویندوز دستی مدیریت می‌شود)
    loop = asyncio.get_running_loop()
    
    def handle_shutdown():
        print("\n🛑 دریافت سیگنال خاموشی. در حال متوقف کردن فرآیندها...")
        shutdown_event.set()

    # در ویندوز ممکن است add_signal_handler خطا بدهد
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            # در ویندوز، signal_handler ثبت نمی‌شود، خطای KeyboardInterrupt در بدنه اصلی هندل می‌شود
            pass

    try:
        await client.start()
        print("✅ با موفقیت به تلگرام متصل شد.")
    except Exception as e:
        print(f"❌ خطا در اتصال به تلگرام: {e}")
        sys.exit(1)
        
    # تعریف تسک‌ها
    worker_task = asyncio.create_task(queue_sender_worker(client))
    web_task = asyncio.create_task(web_fetching_loop())
    
    try:
        # ۱. حالت Bootstrap: واکشی پست‌های ۵ روز اخیر کانال‌های مانیتورینگ
        if BOOTSTRAP_ENABLED:
            print(f"\n⚙️ [حالت BOOTSTRAP] در حال دریافت مطالب {BOOTSTRAP_DAYS} روز اخیر...")
            historical_posts = await fetch_historical_telegram_posts(client, days=BOOTSTRAP_DAYS)
            print(f"📋 تعداد {len(historical_posts)} پست قدیمی یافت شد. ریختن در صف پردازش...")
            
            # Fetch historical announcements from official channels as well
            historical_announcements = await fetch_historical_announcements(client, days=BOOTSTRAP_DAYS)
            print(f"📢 تعداد {len(historical_announcements)} اعلان رسمی قدیمی یافت شد. ادغام و ریختن در صف پردازش...")
            
            # Combine and sort all bootstrap posts by date
            combined_bootstrap = historical_posts + historical_announcements
            combined_bootstrap.sort(key=lambda x: x['date'])
            
            for item in combined_bootstrap:
                if shutdown_event.is_set():
                    break
                while extraction_paused.is_set() and not shutdown_event.is_set():
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
                await process_raw_item(item)
                
            print("✅ پردازش آرشیو تاریخی تمام شد و موارد تایید شده در صف ارسال قرار گرفتند.")
        else:
            print("\n⚙️ [حالت BOOTSTRAP] غیرفعال است.")
        
        if not shutdown_event.is_set():
            # ۲. شروع مانیتورینگ زنده کانال‌های تلگرام
            # برای جلوگیری از بلاک شدن کامل، متد مانیتورینگ زنده را استارت می‌زنیم
            if LIVE_MONITORING_ENABLED:
                await start_telegram_monitoring(client)
                await start_announcements_monitoring(client)
            else:
                print("📡 مانیتورینگ لایو کانال‌های تلگرام غیرفعال است.")
            
        # ۳. منتظر ماندن تا فعال شدن رویداد توقف یا قطع ارتباط تلگرام
        while not shutdown_event.is_set():
            # به صورت دوره‌ای اتصال کلاینت را بررسی می‌کنیم و ۲ ثانیه می‌خوابیم
            if not client.is_connected():
                print("⚠️ قطع ارتباط شناسایی شد! تلاش مجدد...")
                try:
                    await client.connect()
                except Exception as ec:
                    print(f"❌ تلاش برای اتصال مجدد با شکست مواجه شد: {ec}")
            await asyncio.sleep(2)
            
    except (KeyboardInterrupt, SystemExit):
        print("\n⏹️ توقف توسط کاربر (KeyboardInterrupt/SystemExit).")
        shutdown_event.set()
    except Exception as e:
        print(f"❌ خطای غیرمنتظره در حلقه اصلی: {e}")
        shutdown_event.set()
    finally:
        # لغو کارهای در حال اجرا به صورت تمیز
        print("🧹 در حال پاکسازی کارهای پس‌زمینه...")
        worker_task.cancel()
        web_task.cancel()
        
        # منتظر ماندن برای خاموشی تسک‌ها
        await asyncio.gather(worker_task, web_task, return_exceptions=True)
        
        # قطع ارتباط کلاینت
        try:
            if client.is_connected():
                print("🔌 در حال قطع ارتباط کلاینت تلگرام...")
                await client.disconnect()
        except Exception as e:
            print(f"⚠️ خطا در قطع ارتباط تلگرام: {e}")
            
        print("👋 ربات با موفقیت متوقف شد.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 خروج نهایی.")
