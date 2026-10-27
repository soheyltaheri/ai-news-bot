import httpx
import feedparser
import re
from bs4 import BeautifulSoup
from database.dedup import is_processed, mark_as_processed
from processor.ai_engine import process_news_with_ai

async def extract_og_image(url):
    """
    Requests the page using a lightweight request to extract og:image if available.
    """
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
            r = await client.get(url, headers=headers, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                og_image = soup.find('meta', property='og:image')
                if og_image and og_image.get('content'):
                    return og_image['content']
    except Exception as e:
        print(f"⚠️ Warning: Failed to extract og:image from {url}: {e}")
    return None

def extract_media_url_from_entry(entry):
    """
    Attempt to extract images from RSS feeds.
    Look for entry.media_content, entry.links, or <img src="..." in entry.summary.
    """
    # 1. media_content
    if hasattr(entry, 'media_content') and entry.media_content:
        for media in entry.media_content:
            if media.get('medium') == 'image' or 'image' in media.get('type', ''):
                if media.get('url'):
                    return media['url']
        # Fallback to first URL in media_content
        if entry.media_content[0].get('url'):
            return entry.media_content[0]['url']
            
    # 2. links
    if hasattr(entry, 'links') and entry.links:
        for link in entry.links:
            if 'image' in link.get('type', ''):
                if link.get('href'):
                    return link['href']
                    
    # 3. img in summary/description
    summary = entry.get('summary', '')
    if summary:
        soup = BeautifulSoup(summary, 'html.parser')
        img = soup.find('img')
        if img and img.get('src'):
            return img['src']
            
    return None

async def fetch_hacker_news_top():
    print("🔍 در حال بررسی Hacker News...")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get('https://hacker-news.firebaseio.com/v0/topstories.json')
            ids = r.json()[:20]  # 20 خبر اول
            
            news_items = []
            for sid in ids:
                item_id = str(sid)
                if is_processed(f"hn_{item_id}"):
                    continue
                
                story_req = await client.get(f'https://hacker-news.firebaseio.com/v0/item/{sid}.json')
                story = story_req.json()
                
                if 'url' in story and 'title' in story:
                    # برای HN ما معمولا فقط عنوان و لینک داریم، برای پردازش AI بهتره متن هم باشه 
                    # اما برای MVP همینو میدیم به AI
                    news_items.append({
                        'id': f"hn_{item_id}",
                        'raw_text': f"Title: {story['title']}\nURL: {story['url']}",
                        'url': story['url'],
                        'source_name': 'Hacker News',
                        'media_url': None # Hacker News مدیا نداره معمولا
                    })
            return news_items
    except Exception as e:
        print(f"❌ خطا در Hacker News: {e}")
        return []

async def fetch_rss_feeds(rss_urls):
    print("🔍 در حال بررسی RSS Feeds...")
    news_items = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]: # 10 خبر اول از هر فید
                if is_processed(entry.link):
                    continue
                
                # First extract from RSS fields
                media_url = extract_media_url_from_entry(entry)
                
                # If not found, attempt og:image from the entry link
                if not media_url:
                    media_url = await extract_og_image(entry.link)

                news_items.append({
                    'id': entry.link,
                    'raw_text': f"Title: {entry.title}\nSummary: {entry.get('summary', '')}",
                    'url': entry.link,
                    'source_name': getattr(feed.feed, 'title', 'RSS News'),
                    'media_url': media_url
                })
        except Exception as e:
            print(f"❌ خطا در پردازش RSS {url}: {e}")
    return news_items
