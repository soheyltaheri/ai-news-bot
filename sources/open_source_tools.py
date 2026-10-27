# sources/open_source_tools.py
"""
Open-source tools integration for AI News Aggregator Bot.

Provides article content extraction, summarization stubs, language detection,
basic Persian-to-English translation, and integration hooks for the main bot.

Researched tools (see open_source_requirements.txt for install list):

  CONTENT EXTRACTION:
  - trafilatura (6.3k ★) - GitHub: adbar/trafilatura
    Best-in-class: extracts main text, metadata, and handles noisy HTML.
    Actively maintained (commits 4 days ago as of research). Pure Python,
    no headless browser needed. Preferred over newspaper3k (unmaintained).

  - newspaper3k (13.6k ★) - GitHub: codelucas/newspaper
    Classic extractor. Unmaintained since 2020 but still widely used.
    Good as fallback; trafilatura is primary.

  RSS / FEED PARSERS:
  - feedparser (already in requirements.txt) - parse RSS/Atom feeds
  - feedparser is battle-tested; no need for alternatives at this scale.

  LANGUAGE DETECTION:
  - langdetect (3.5k ★) - GitHub: Mimino666/langdetect
    Google's language-detection library port. Supports 55+ languages
    including Persian (fa). Lightweight, fast, pure Python.

  TRANSLATION:
  - argos-translate (5k ★) - GitHub: argosopentech/argos-translate
    Offline open-source translator (fa→en supported via downloaded packages).
    Alternative: use the bot's existing AI router for translation.
  - deep-translator (3.2k ★) - GitHub: nidhaloff/deep-translator
    Wrapper around multiple translation APIs (Google, MyMemory, etc).
    Free tiers available, no API key needed for basic use.

  TEXT PROCESSING:
  - beautifulsoup4 (already in requirements.txt via web_sources.py)
  - lxml - fast HTML/XML parser used by trafilatura internally

  TELEGRAM MONITORING (beyond Telethon):
  - telethon (already used) - the gold standard for Telegram client API
  - pytgcats / tgbotapi - limited; Telethon is sufficient
  - No better alternative found; Telethon handles channel monitoring well.

  NEWS AGGREGATION BOTS (reference):
  - hrnrxb/AI-News-Aggregator-Bot (22★) - Similar architecture: Telegram
    bot aggregating AI news via RSS + HN + blogs. Uses feedparser + httpx.
"""

import httpx
import logging
import re
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Lazy imports — these libraries are optional heavy dependencies.
# The functions below gracefully degrade if a library is not installed.
# ──────────────────────────────────────────────────────────────────────────────

_trafilatura = None
_langdetect = None
_deep_translator = None
_newspaper = None


def _get_trafilatura():
    """Lazy-load trafilatura."""
    global _trafilatura
    if _trafilatura is None:
        try:
            import trafilatura
            _trafilatura = trafilatura
        except ImportError:
            logger.warning("trafilatura not installed. Run: pip install trafilatura")
    return _trafilatura


def _get_langdetect():
    """Lazy-load langdetect."""
    global _langdetect
    if _langdetect is None:
        try:
            import langdetect
            _langdetect = langdetect
        except ImportError:
            logger.warning("langdetect not installed. Run: pip install langdetect")
    return _langdetect


def _get_deep_translator():
    """Lazy-load deep-translator."""
    global _deep_translator
    if _deep_translator is None:
        try:
            from deep_translator import GoogleTranslator
            _deep_translator = GoogleTranslator
        except ImportError:
            logger.warning(
                "deep-translator not installed. Run: pip install deep-translator"
            )
    return _deep_translator


def _get_newspaper():
    """Lazy-load newspaper3k as fallback."""
    global _newspaper
    if _newspaper is None:
        try:
            import newspaper
            _newspaper = newspaper
        except ImportError:
            logger.warning("newspaper3k not installed. Run: pip install newspaper3k")
    return _newspaper


# ──────────────────────────────────────────────────────────────────────────────
# ARTICLE CONTENT EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

async def extract_article_content(
    url: str,
    timeout: float = 15.0,
    fetch_metadata: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Extract clean article text and optional metadata from a URL.

    Uses trafilatura (primary) with newspaper3k as fallback.

    Args:
        url: The article URL to fetch and extract from.
        timeout: HTTP request timeout in seconds.
        fetch_metadata: If True, also extract title, author, date, etc.

    Returns:
        Dict with keys:
            text (str):       The extracted main article text.
            title (str):      Article title (if found).
            author (str):     Author name (if found).
            date (str):       Publication date (if found).
            description (str): Short description / summary (if found).
            sitename (str):   The site name (if found).
            url (str):        The original URL.
            source (str):     Which library extracted it ('trafilatura' or 'newspaper3k').
        None if extraction fails entirely.
    """
    if not url or not url.startswith("http"):
        logger.warning(f"Invalid URL for article extraction: {url}")
        return None

    # Fetch raw HTML
    html = await _fetch_html(url, timeout)
    if not html:
        return None

    # Try trafilatura first (best quality)
    result = _extract_with_trafilatura(html, url, fetch_metadata)
    if result and result.get("text"):
        return result

    # Fallback to newspaper3k
    result = _extract_with_newspaper(html, url)
    if result and result.get("text"):
        return result

    logger.warning(f"All extraction methods failed for {url}")
    return None


async def _fetch_html(url: str, timeout: float = 15.0) -> Optional[str]:
    """Fetch raw HTML from a URL using httpx."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching {url}: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.error(f"Request error fetching {url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
    return None


def _extract_with_trafilatura(
    html: str, url: str, fetch_metadata: bool = True
) -> Optional[Dict[str, Any]]:
    """Extract article content using trafilatura."""
    trafilatura = _get_trafilatura()
    if trafilatura is None:
        return None

    try:
        if fetch_metadata:
            result = trafilatura.extract(
                html,
                url=url,
                output_format="json",
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            if result:
                import json
                data = json.loads(result)
                return {
                    "text": data.get("text", ""),
                    "title": data.get("title", ""),
                    "author": data.get("author", ""),
                    "date": data.get("date", ""),
                    "description": data.get("description", ""),
                    "sitename": data.get("sitename", ""),
                    "url": url,
                    "source": "trafilatura",
                }
        else:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            if text:
                return {
                    "text": text,
                    "title": "",
                    "author": "",
                    "date": "",
                    "description": "",
                    "sitename": "",
                    "url": url,
                    "source": "trafilatura",
                }
    except Exception as e:
        logger.error(f"trafilatura extraction failed for {url}: {e}")
    return None


def _extract_with_newspaper(html: str, url: str) -> Optional[Dict[str, Any]]:
    """Extract article content using newspaper3k (fallback)."""
    newspaper = _get_newspaper()
    if newspaper is None:
        return None

    try:
        article = newspaper.Article(url)
        article.set_html(html)
        article.parse()

        text = article.text or ""
        if len(text.strip()) < 50:
            # Too short to be a real article extraction
            return None

        return {
            "text": text,
            "title": article.title or "",
            "author": ", ".join(article.authors) if article.authors else "",
            "date": article.publish_date.isoformat() if article.publish_date else "",
            "description": article.meta_description or "",
            "sitename": "",
            "url": url,
            "source": "newspaper3k",
        }
    except Exception as e:
        logger.error(f"newspaper3k extraction failed for {url}: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# ARTICLE SUMMARIZATION
# ──────────────────────────────────────────────────────────────────────────────

def summarize_article(
    text: str,
    max_sentences: int = 5,
    max_chars: int = 1000,
) -> str:
    """
    Extractive summarization: picks the most important sentences.

    Uses a simple TF-IDF-inspired scoring heuristic (no ML model needed).
    For higher quality, the bot's AI router can be used instead.

    Args:
        text: The full article text.
        max_sentences: Maximum number of sentences in the summary.
        max_chars: Maximum character count for the summary.

    Returns:
        A summary string with key sentences.
    """
    if not text or len(text.strip()) < 100:
        return text or ""

    # Split into sentences (handles common patterns)
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return text[:max_chars]

    # Score sentences by word frequency (simple extractive approach)
    word_freq = _compute_word_frequencies(text)
    scored = []
    for i, sentence in enumerate(sentences):
        words = re.findall(r'\b\w+\b', sentence.lower())
        score = sum(word_freq.get(w, 0) for w in words)
        # Slight bonus for earlier sentences (lead bias)
        position_bonus = 1.0 / (1 + i * 0.1)
        # Bonus for sentences containing numbers or quotes (often important)
        detail_bonus = 1.2 if re.search(r'\d|"|«|»', sentence) else 1.0
        scored.append((i, score * position_bonus * detail_bonus))

    # Pick top N sentences, sorted by original order
    scored.sort(key=lambda x: x[1], reverse=True)
    top_indices = sorted(idx for idx, _ in scored[:max_sentences])

    summary_parts = [sentences[i] for i in top_indices]
    summary = " ".join(summary_parts)

    return summary[:max_chars]


def _split_sentences(text: str) -> list:
    """Split text into sentences, handling common edge cases."""
    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?؟!])\s+', text)
    # Filter out very short fragments (< 5 chars)
    return [p.strip() for p in parts if len(p.strip()) >= 5]


def _compute_word_frequencies(text: str) -> Dict[str, float]:
    """Compute normalized word frequencies, excluding common stop words."""
    # Common English + Persian stop words (compact set)
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "and", "but", "or",
        "nor", "not", "so", "very", "just", "than", "too", "also", "this",
        "that", "these", "those", "i", "me", "my", "we", "our", "you",
        "your", "he", "him", "his", "she", "her", "it", "its", "they",
        "them", "their", "what", "which", "who", "whom", "when", "where",
        "why", "how", "all", "each", "every", "both", "few", "more",
        "most", "other", "some", "such", "only", "own", "same",
        # Persian stop words
        "و", "در", "به", "از", "که", "این", "را", "با", "است", "برای",
        "آن", "یک", "خود", "تا", "کرد", "بر", "هم", "یا", "اما", "باید",
        "آنها", "شود", "شد", "بود", "دارد", "می", "پس", "اگر", "همه",
        "دو", "هیچ", "بیشتر", "همین", "پیش", "بعد", "چه", "هستند",
    }
    words = re.findall(r'\b[a-zA-Z\u0600-\u06FF]+\b', text.lower())
    freq = {}
    for w in words:
        if w not in stop_words and len(w) > 2:
            freq[w] = freq.get(w, 0) + 1
    # Normalize
    max_freq = max(freq.values()) if freq else 1
    return {k: v / max_freq for k, v in freq.items()}


# ──────────────────────────────────────────────────────────────────────────────
# LANGUAGE DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def detect_language(text: str) -> Dict[str, Any]:
    """
    Detect the language of a text string.

    Args:
        text: The text to analyze.

    Returns:
        Dict with keys:
            language (str):  ISO 639-1 code (e.g. 'fa', 'en', 'ar').
            confidence (float): Confidence score 0.0–1.0 (approximate).
            is_persian (bool):   True if detected as Persian/Farsi.
            is_english (bool):   True if detected as English.
            is_arabic (bool):    True if detected as Arabic.
    """
    result = {
        "language": "unknown",
        "confidence": 0.0,
        "is_persian": False,
        "is_english": False,
        "is_arabic": False,
    }

    if not text or len(text.strip()) < 10:
        return result

    # Quick heuristic: check for Persian characters first
    sample = text[:2000]
    persian_chars = len(re.findall(r'[\u0600-\u06FF]', sample))
    arabic_chars = len(re.findall(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]', sample))
    total_chars = len(re.findall(r'\S', sample))

    if total_chars > 0:
        persian_ratio = persian_chars / total_chars
        if persian_ratio > 0.3:
            result["language"] = "fa"
            result["confidence"] = min(0.95, 0.5 + persian_ratio)
            result["is_persian"] = True
            return result

    # Fall back to langdetect
    langdetect = _get_langdetect()
    if langdetect is not None:
        try:
            detected = langdetect.detect(text[:2000])
            result["language"] = detected
            result["confidence"] = 0.85  # langdetect doesn't return confidence
            result["is_persian"] = detected == "fa"
            result["is_english"] = detected == "en"
            result["is_arabic"] = detected == "ar"
            return result
        except langdetect.LangDetectException:
            pass  # Not enough text or ambiguous

    # Character-range fallback
    if persian_chars > 0:
        result["language"] = "fa"
        result["confidence"] = 0.6
        result["is_persian"] = True
    elif re.search(r'[a-zA-Z]', sample):
        result["language"] = "en"
        result["confidence"] = 0.7
        result["is_english"] = True

    return result


# ──────────────────────────────────────────────────────────────────────────────
# PERSIAN → ENGLISH TRANSLATION (BASIC)
# ──────────────────────────────────────────────────────────────────────────────

async def translate_persian_to_english(
    text: str,
    max_length: int = 4500,
) -> Optional[str]:
    """
    Translate Persian (Farsi) text to English.

    Tries deep-translator (Google Translate free tier) first.
    Falls back to a truncation + note if translation is unavailable.

    Args:
        text: Persian text to translate.
        max_length: Max characters to send for translation (API limits).

    Returns:
        English translation string, or None if translation fails.
    """
    if not text or len(text.strip()) < 10:
        return text

    # Truncate to API limits
    if len(text) > max_length:
        text = text[:max_length]

    GoogleTranslator = _get_deep_translator()
    if GoogleTranslator is not None:
        try:
            translator = GoogleTranslator(source="fa", target="en")
            translated = translator.translate(text)
            if translated:
                return translated
        except Exception as e:
            logger.error(f"deep-translator failed: {e}")

    # Fallback: return original with language note
    return f"[FA → EN translation unavailable]\n{text}"


# ──────────────────────────────────────────────────────────────────────────────
# INTEGRATION HOOKS — called from main bot pipeline
# ──────────────────────────────────────────────────────────────────────────────

async def enhance_news_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhance a raw news item with full article content, language info,
    and optional English translation for Persian articles.

    This is the main integration hook. Call it in web_sources.py or
    telegram.py after obtaining a raw news item.

    Args:
        item: Dict with at least 'raw_text', 'url', and 'source_name'.
              Optional keys: 'title', 'media_url'.

    Returns:
        The same item dict, enriched with:
            extracted_text (str):  Full article body (if extraction succeeded).
            detected_language (str): ISO 639-1 language code.
            english_text (str):    English version (for Persian articles).
            summary (str):         Extractive summary of the article.
            extraction_source (str): Which library extracted the content.
    """
    url = item.get("url", "")
    raw_text = item.get("raw_text", "")

    # 1. Extract full article content from URL
    extracted = None
    if url and url.startswith("http"):
        extracted = await extract_article_content(url, timeout=15.0)

    # Use extracted text if available, otherwise fall back to raw_text
    full_text = ""
    if extracted and extracted.get("text"):
        full_text = extracted["text"]
        item["extracted_text"] = full_text
        item["extraction_source"] = extracted.get("source", "unknown")
        # Merge metadata if missing
        if not item.get("title") and extracted.get("title"):
            item["title"] = extracted["title"]
    else:
        full_text = raw_text

    # 2. Detect language
    lang_info = detect_language(full_text)
    item["detected_language"] = lang_info.get("language", "unknown")

    # 3. Summarize the article
    item["summary"] = summarize_article(full_text, max_sentences=5, max_chars=800)

    # 4. Translate if Persian
    if lang_info.get("is_persian") and full_text:
        english = await translate_persian_to_english(full_text[:2000])
        item["english_text"] = english or ""

    return item


async def batch_enhance_news_items(
    items: list,
    concurrency_limit: int = 5,
) -> list:
    """
    Enhance multiple news items with content extraction.

    Processes items with limited concurrency to avoid overwhelming
    remote servers.

    Args:
        items: List of news item dicts.
        concurrency_limit: Max concurrent HTTP requests.

    Returns:
        List of enhanced news items.
    """
    import asyncio

    semaphore = asyncio.Semaphore(concurrency_limit)
    enhanced = []

    async def _enhance_with_semaphore(item):
        async with semaphore:
            return await enhance_news_item(item)

    tasks = [_enhance_with_semaphore(item) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Enhancement failed for item {i}: {result}")
            enhanced.append(items[i])  # Keep original on failure
        else:
            enhanced.append(result)

    return enhanced


# ──────────────────────────────────────────────────────────────────────────────
# UTILITY: Check which optional libraries are available
# ──────────────────────────────────────────────────────────────────────────────

def get_available_tools() -> Dict[str, bool]:
    """
    Returns a dict showing which optional libraries are installed.
    Useful for diagnostics and startup logging.
    """
    return {
        "trafilatura": _get_trafilatura() is not None,
        "newspaper3k": _get_newspaper() is not None,
        "langdetect": _get_langdetect() is not None,
        "deep_translator": _get_deep_translator() is not None,
    }


def print_tool_status():
    """Print which optional tools are available. Call at bot startup."""
    tools = get_available_tools()
    status_parts = []
    for name, available in tools.items():
        icon = "✅" if available else "❌"
        status_parts.append(f"{icon} {name}")
    print(f"🔧 Open-source tools: {' | '.join(status_parts)}")
