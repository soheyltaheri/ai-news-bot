# processor/ai_engine.py
import json
import httpx
import re
import asyncio
import logging
from config import ROUTER_BASE_URL, ROUTER_API_KEY, MODEL_NAME

logger = logging.getLogger(__name__)

# Constants for robust API calls
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0  # seconds
TIMEOUT = 30.0         # seconds

SYSTEM_PROMPT = """
تو یک ویرایشگر اخبار هوش مصنوعی و تکنولوژی هستی.
یک خبر خام به تو داده می شود. وظیفه تو این است که آن را پردازش کرده و یک پست جذاب برای کانال تلگرام تولید کنی.

خروجی تو باید **فقط و فقط** یک شی JSON معتبر با ساختار زیر باشد، بدون هیچ متن اضافه یا قالب بندی markdown در ابتدا و انتها:
{
  "title": "عنوان جذاب و کوتاه با ایموجی",
  "category": "نام دسته‌بندی با ایموجی (مانند: 🤖 هوش مصنوعی، 💻 برنامه نویسی، 🚗 خودروهای برقی، 🔬 علم و تحقیق، 🔒 امنیت و حریم خصوصی و غیره)",
  "body": "یک تحلیل دقیق و تفصیلی به زبان فارسی بین ۳۰۰ تا ۴۵۰ کلمه، ساختاریافته در قالب پاراگراف‌های مشخص همراه با ایموجی‌های مناسب. متن باید شامل نکات کلیدی خبر، دلیل اهمیت آن (چرا مهم است) و یک نگاه به آینده یا تحلیل اثرات کلیدی و حیاتی باشد.",
  "rating": 4, 
  "is_relevant": true
}

قوانین:
1. مقدار `rating` باید یک عدد بین 1 تا 5 باشد (5 یعنی خبر بسیار مهم و فوق العاده است).
2. `is_relevant`: اگر خبر مربوط به هوش مصنوعی یا تکنولوژی های پیشرفته نیست، آن را false قرار بده تا نادیده گرفته شود.
3. لحن باید حرفه ای اما جذاب برای مخاطب شبکه اجتماعی باشد.
"""

def extract_and_parse_json(text):
    """
    Attempts to extract JSON from a response string that may contain markdown formatting,
    extra whitespace, or prefix/suffix garbage, and parse it safely.
    """
    if not text:
        return None
        
    text = text.strip()
    
    # Try parsing directly first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
        
    # Remove standard markdown code blocks if any: ```json ... ``` or ``` ... ```
    cleaned = text
    # Regex to extract text inside ```json ... ``` or ``` ... ```
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if code_block_match:
        cleaned = code_block_match.group(1).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
            
    # As a last resort, search for the first '{' and last '}'
    start_idx = cleaned.find('{')
    end_idx = cleaned.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_candidate = cleaned[start_idx:end_idx + 1]
        try:
            return json.loads(json_candidate)
        except json.JSONDecodeError:
            pass
            
    return None

async def process_news_with_ai(raw_text, source_name):
    """
    Processes raw news text using the configured AI router (with MiMo model).
    Implements robust retry mechanisms, timeout handling, and bulletproof JSON extraction/parsing.
    """
    if not ROUTER_API_KEY:
        print(f"⚠️ کلید API تنظیم نشده، پردازش AI رد شد برای خبر از {source_name}")
        return None

    url = f"{ROUTER_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {ROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"منبع: {source_name}\n\nمتن خبر:\n{raw_text}"}
        ],
        "response_format": {"type": "json_object"}
    }

    # Setup AsyncClient with limits & connection pooling
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(limits=limits, timeout=TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                # Check response.json() directly since httpx handles response mapping.
                # However, if the server returns stream artifacts like "data: [DONE]" inside the text,
                # we parse the raw text by scrubbing stream indicators.
                try:
                    data = response.json()
                except Exception:
                    response_text = response.text.strip()
                    # Remove Server-Sent Events markers if any leaked into the response
                    if "data: [DONE]" in response_text:
                        response_text = response_text.replace("data: [DONE]", "").strip()
                    
                    # Try parsing the clean text
                    try:
                        data = json.loads(response_text)
                    except json.JSONDecodeError:
                        # Extract first valid JSON object as fallback
                        start_idx = response_text.find('{')
                        end_idx = response_text.rfind('}')
                        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                            data = json.loads(response_text[start_idx:end_idx + 1])
                        else:
                            raise ValueError("No valid JSON structure found in API response text")
                
                # Check structure validity
                if not data or 'choices' not in data or len(data['choices']) == 0:
                    raise KeyError("Malformed API response structure (missing choices)")
                    
                message = data['choices'][0].get('message', {})
                content = message.get('content', '')
                
                # Extract and parse JSON with advanced serialization/cleanup
                parsed_data = extract_and_parse_json(content)
                if parsed_data is None:
                    # Log raw content when JSON extraction fails to aid in debugging
                    logger.warning(f"Failed to parse JSON from response on attempt {attempt}: {repr(content)}")
                    raise ValueError("Failed to extract valid JSON from LLM response")
                    
                # Schema validation & type enforcement
                category_val = parsed_data.get("category")
                if category_val is None or str(category_val).strip() == "":
                    category_val = "🔥 تکنولوژی"
                else:
                    category_val = str(category_val).strip()

                validated_data = {
                    "title": str(parsed_data.get("title", "خبر جدید")),
                    "category": category_val,
                    "body": str(parsed_data.get("body", "")),
                    "rating": parsed_data.get("rating"),
                    "is_relevant": bool(parsed_data.get("is_relevant", False))
                }
                
                # Coerce and clamp rating to integer in range 1-5
                try:
                    rating_val = int(validated_data["rating"])
                    validated_data["rating"] = max(1, min(5, rating_val))
                except (ValueError, TypeError):
                    # Default rating if unparseable
                    validated_data["rating"] = 3
                    
                return validated_data
                
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as e:
            logger.error(f"Attempt {attempt}/{MAX_RETRIES} failed to process news from {source_name}: {e}")
            if attempt == MAX_RETRIES:
                break
            # Exponential backoff before retry
            sleep_time = INITIAL_BACKOFF * (2 ** (attempt - 1))
            await asyncio.sleep(sleep_time)
            
    print(f"❌ خطا در پردازش AI خبر پس از {MAX_RETRIES} تلاش.")
    return None
