# login.py
import asyncio
import os
import sys
from telethon import TelegramClient
from config import API_ID, API_HASH, SESSION_PATH

async def main():
    print("📢 در حال راه‌اندازی فرآیند احراز هویت تلگرام...")
    print(f"📁 مسیر فایل سشن: {SESSION_PATH}")
    print(f"🔑 API_ID: {API_ID}")
    
    if not API_ID or not API_HASH:
        print("❌ خطا: API_ID یا API_HASH در فایل config.py یا .env تنظیم نشده است!")
        sys.exit(1)

    # ایجاد کلاینت تلگرام
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    
    # شروع پروسه لاگین تعاملی
    # این متد در صورتی که سشن معتبر نباشد، از شما شماره تلفن، کد تایید و در صورت فعال بودن 2FA پسورد را می‌خواهد.
    try:
        await client.start()
        
        # بررسی وضعیت لاگین
        if await client.is_user_authorized():
            me = await client.get_me()
            print("\n✅ احراز هویت با موفقیت انجام شد!")
            print(f"👤 نام کاربری: @{me.username if me.username else 'ندارد'}")
            print(f"📞 شماره تلفن: +{me.phone if me.phone else 'نامشخص'}")
            print(f"🏷️ نام اکانت: {me.first_name} {me.last_name if me.last_name else ''}")
            print("\n💾 فایل سشن با موفقیت در پوشه sessions ذخیره شد. اکنون می‌توانید main.py را ران کنید.")
        else:
            print("❌ خطا: احراز هویت کامل نشد.")
            
    except Exception as e:
        print(f"\n❌ خطا در فرآیند لاگین: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    # اجرای حلقه رویدادهای ناهمگام
    asyncio.run(main())
